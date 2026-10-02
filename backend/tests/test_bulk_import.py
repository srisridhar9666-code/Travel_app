"""
Parsing and validating a bulk-import CSV.

Preview is the safety net before a hundred accounts get created, so its counts
and per-row messages are what these tests pin down.
"""
import pytest
from sqlalchemy import create_engine

from app.core.enums import Designation, Gender, Role, UserStatus
from app.database import Base
from app.models.department import Department
from app.models.user import User
from app.services import bulk_import, locations

TENANT = "designboxed"
HEADER = "full_name,email,role,designation,gender,phone,employee_code,base_location\n"


def parse(body: str, db):
    return bulk_import.parse(body.encode("utf-8"), db, TENANT)


class TestHappyPath:
    def test_a_clean_row_imports(self, db):
        result = parse(
            HEADER + "Ravi Kumar,ravi@designboxed.com,GROUND_STAFF,EXECUTIVE,MALE,+91 90000 00001,DB-1,Pune\n",
            db,
        )
        assert result.importable == 1
        assert result.skipped == 0
        row = result.rows[0]
        assert row.full_name == "Ravi Kumar"
        assert row.email == "ravi@designboxed.com"
        assert row.role is Role.GROUND_STAFF
        assert row.designation is Designation.EXECUTIVE
        assert row.gender is Gender.MALE
        assert row.errors == []

    def test_optional_columns_may_be_blank(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.importable == 1
        assert result.rows[0].role is Role.GROUND_STAFF  # defaulted
        assert result.rows[0].department is None
        assert result.rows[0].base_state is None

    def test_blank_lines_are_ignored(self, db):
        result = parse(HEADER + "\n,,,,,,,\nRavi Kumar,ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.total == 1

    def test_headers_are_case_and_space_insensitive(self, db):
        header = "Full Name,Email,Role,Designation,Gender,Phone,Employee Code,Base Location\n"
        result = parse(header + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.importable == 1

    def test_excel_utf8_bom_is_handled(self, db):
        """Excel writes a BOM, which would otherwise corrupt the first header."""
        body = (HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n").encode("utf-8-sig")
        result = bulk_import.parse(body, db, TENANT)
        assert result.file_errors == []
        assert result.importable == 1

    def test_names_are_tidied(self, db):
        result = parse(HEADER + "  Ravi   Kumar ,ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.rows[0].full_name == "Ravi Kumar"

    def test_email_is_lowercased(self, db):
        result = parse(HEADER + "Ravi Kumar,RAVI@DesignBoxed.com,,,MALE,,,\n", db)
        assert result.rows[0].email == "ravi@designboxed.com"


class TestRowErrors:
    def test_missing_name(self, db):
        result = parse(HEADER + ",ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.importable == 0
        assert any("full_name" in e for e in result.rows[0].errors)

    def test_missing_email(self, db):
        result = parse(HEADER + "Ravi Kumar,,,,,,,\n", db)
        assert any("email is required" in e for e in result.rows[0].errors)

    def test_malformed_email(self, db):
        result = parse(HEADER + "Ravi Kumar,not-an-email,,,,,,\n", db)
        assert any("valid email" in e for e in result.rows[0].errors)

    def test_unknown_role_names_the_valid_options(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,WIZARD,,,,,\n", db)
        message = " ".join(result.rows[0].errors)
        assert "role must be one of" in message
        assert "GROUND_STAFF" in message

    def test_unknown_gender(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,MARTIAN,,,\n", db)
        assert any("gender must be" in e for e in result.rows[0].errors)

    def test_duplicate_inside_the_file(self, db):
        body = HEADER + (
            "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n"
            "Ravi Again,ravi@designboxed.com,,,MALE,,,\n"
        )
        result = parse(body, db)
        assert result.importable == 1
        assert any("more than once" in e for e in result.rows[1].errors)

    def test_email_already_in_the_database(self, db):
        db.add(
            User(
                tenant_id=TENANT,
                email="ravi@designboxed.com",
                full_name="Ravi Kumar",
                role=Role.GROUND_STAFF,
                password_hash="x",
            )
        )
        db.commit()
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.importable == 0
        assert any("already has an account" in e for e in result.rows[0].errors)

    def test_one_bad_row_does_not_sink_the_others(self, db):
        body = HEADER + (
            "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n"
            "Broken,not-an-email,,,,,,\n"
            "Anita Desai,anita@designboxed.com,,,MALE,,,\n"
        )
        result = parse(body, db)
        assert result.importable == 2
        assert result.skipped == 1

    def test_line_numbers_point_at_the_spreadsheet_row(self, db):
        """Line 1 is the header, so the first data row is line 2."""
        body = HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\nBroken,nope,,,,,,\n"
        result = parse(body, db)
        assert [row.line for row in result.rows] == [2, 3]


class TestGender:
    """Only Male or Female can be recorded: room sharing depends on it, and a
    blank would quietly become 'never shares' for good."""

    def test_blank_gender_is_an_error(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,,,,\n", db)
        assert result.importable == 0
        assert result.rows[0].gender is None
        assert "gender is required (MALE or FEMALE)" in result.rows[0].errors

    @pytest.mark.parametrize("typed, expected", [
        ("f", Gender.FEMALE), ("Female", Gender.FEMALE), ("M", Gender.MALE), ("male", Gender.MALE),
    ])
    def test_short_and_mixed_case_forms_are_accepted(self, db, typed, expected):
        result = parse(HEADER + f"Anita Desai,anita@designboxed.com,,,{typed},,,\n", db)
        assert result.importable == 1
        assert result.rows[0].gender is expected

    @pytest.mark.parametrize("typed", ["OTHER", "UNDISCLOSED"])
    def test_the_old_values_are_refused(self, db, typed):
        result = parse(HEADER + f"Anita Desai,anita@designboxed.com,,,{typed},,,\n", db)
        assert "gender must be MALE or FEMALE" in result.rows[0].errors


class TestWarnings:

    def test_admin_rows_are_called_out(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,ADMIN,,MALE,,,\n", db)
        assert result.importable == 1
        assert any("ADMIN" in w for w in result.rows[0].warnings)


class TestFileErrors:
    def test_missing_required_headers(self, db):
        result = parse("name,mail\nfoo,bar\n", db)
        assert result.rows == []
        assert any("Missing required column" in e for e in result.file_errors)

    def test_unknown_columns_are_reported_but_not_fatal(self, db):
        header = HEADER.rstrip("\n") + ",favourite_colour\n"
        result = parse(header + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,,blue\n", db)
        assert result.importable == 1
        assert any("favourite_colour" in e for e in result.file_errors)

    def test_undecodable_file(self, db):
        result = bulk_import.parse(b"\xff\xfe\x00\x00\xff", db, TENANT)
        # Falls through to latin-1, which decodes anything - so this lands as a
        # header error rather than an encoding one. Either way, nothing imports.
        assert result.importable == 0


class TestTemplate:
    def test_template_has_every_column_and_an_example(self):
        csv_text = bulk_import.template_csv()
        lines = csv_text.strip().splitlines()
        assert len(lines) == 2
        for column in bulk_import.IMPORT_COLUMNS:
            assert column in lines[0]

    def test_template_round_trips_through_the_parser(self, db):
        """The example row we hand people must actually import."""
        result = bulk_import.parse(bulk_import.template_csv().encode(), db, TENANT)
        assert result.importable == 1
        assert result.rows[0].errors == []


FULL_HEADER = (
    "full_name,email,role,designation,gender,phone,employee_code,department,base_state,base_location\n"
)


class TestDepartmentsAndPlaces:
    def test_a_new_department_is_announced_once(self, db):
        body = FULL_HEADER + (
            "Ravi Kumar,ravi@designboxed.com,,,MALE,,,Field Operations,,\n"
            "Anita Desai,anita@designboxed.com,,,FEMALE,,,field  operations,,\n"
        )
        result = parse(body, db)
        assert result.importable == 2
        assert [r.department for r in result.rows] == ["Field Operations", "field operations"]
        warned = [w for r in result.rows for w in r.warnings if "will be created" in w]
        assert warned == ["Department “Field Operations” will be created"]

    def test_an_existing_department_is_not_announced(self, db):
        db.add(Department(tenant_id=TENANT, name="Finance"))
        db.commit()
        result = parse(FULL_HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,FINANCE,,\n", db)
        assert result.rows[0].department == "FINANCE"
        assert result.rows[0].warnings == []

    def test_base_place_is_matched_to_the_list(self, db):
        locations.seed(db, TENANT)
        db.commit()
        result = parse(
            FULL_HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,,telangana,hyd\n", db
        )
        row = result.rows[0]
        assert (row.base_state, row.base_location) == ("Telangana", "Hyderabad")

    def test_a_bad_phone_is_a_row_error(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,12345,,\n", db)
        assert result.importable == 0
        assert any(e.startswith("phone:") for e in result.rows[0].errors)

    def test_a_deleted_accounts_email_says_restore(self, db):
        db.add(User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                    role=Role.GROUND_STAFF, password_hash="x", status=UserStatus.DELETED))
        db.commit()
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,MALE,,,\n", db)
        assert result.importable == 0
        assert any("deleted account" in e for e in result.rows[0].errors)
