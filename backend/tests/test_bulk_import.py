"""
Parsing and validating a bulk-import CSV.

Preview is the safety net before a hundred accounts get created, so its counts
and per-row messages are what these tests pin down.
"""
import pytest
from sqlalchemy import create_engine

from app.core.enums import Designation, Gender, Role
from app.database import Base
from app.models.user import User
from app.services import bulk_import

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
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,,,,\n", db)
        assert result.importable == 1
        assert result.rows[0].role is Role.GROUND_STAFF  # defaulted
        assert result.rows[0].gender is Gender.UNDISCLOSED

    def test_blank_lines_are_ignored(self, db):
        result = parse(HEADER + "\n,,,,,,,\nRavi Kumar,ravi@designboxed.com,,,,,,\n", db)
        assert result.total == 1

    def test_headers_are_case_and_space_insensitive(self, db):
        header = "Full Name,Email,Role,Designation,Gender,Phone,Employee Code,Base Location\n"
        result = parse(header + "Ravi Kumar,ravi@designboxed.com,,,,,,\n", db)
        assert result.importable == 1

    def test_excel_utf8_bom_is_handled(self, db):
        """Excel writes a BOM, which would otherwise corrupt the first header."""
        body = (HEADER + "Ravi Kumar,ravi@designboxed.com,,,,,,\n").encode("utf-8-sig")
        result = bulk_import.parse(body, db, TENANT)
        assert result.file_errors == []
        assert result.importable == 1

    def test_names_are_tidied(self, db):
        result = parse(HEADER + "  Ravi   Kumar ,ravi@designboxed.com,,,,,,\n", db)
        assert result.rows[0].full_name == "Ravi Kumar"

    def test_email_is_lowercased(self, db):
        result = parse(HEADER + "Ravi Kumar,RAVI@DesignBoxed.com,,,,,,\n", db)
        assert result.rows[0].email == "ravi@designboxed.com"


class TestRowErrors:
    def test_missing_name(self, db):
        result = parse(HEADER + ",ravi@designboxed.com,,,,,,\n", db)
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
            "Ravi Kumar,ravi@designboxed.com,,,,,,\n"
            "Ravi Again,ravi@designboxed.com,,,,,,\n"
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
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,,,,\n", db)
        assert result.importable == 0
        assert any("already has an account" in e for e in result.rows[0].errors)

    def test_one_bad_row_does_not_sink_the_others(self, db):
        body = HEADER + (
            "Ravi Kumar,ravi@designboxed.com,,,,,,\n"
            "Broken,not-an-email,,,,,,\n"
            "Anita Desai,anita@designboxed.com,,,,,,\n"
        )
        result = parse(body, db)
        assert result.importable == 2
        assert result.skipped == 1

    def test_line_numbers_point_at_the_spreadsheet_row(self, db):
        """Line 1 is the header, so the first data row is line 2."""
        body = HEADER + "Ravi Kumar,ravi@designboxed.com,,,,,,\nBroken,nope,,,,,,\n"
        result = parse(body, db)
        assert [row.line for row in result.rows] == [2, 3]


class TestWarnings:
    def test_blank_gender_warns_about_room_sharing(self, db):
        result = parse(HEADER + "Ravi Kumar,ravi@designboxed.com,,,,,,\n", db)
        assert result.importable == 1  # a warning, not an error
        assert any("separate room" in w for w in result.rows[0].warnings)

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
        result = parse(header + "Ravi Kumar,ravi@designboxed.com,,,,,,,blue\n", db)
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
