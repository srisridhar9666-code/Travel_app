"""
Encryption, masking and fingerprinting of identity document numbers.
"""
import pytest

from app.core import pii


class TestEncryption:
    def test_round_trip(self):
        assert pii.decrypt(pii.encrypt("4321 8765 2109")) == "4321 8765 2109"

    def test_ciphertext_hides_the_number(self):
        token = pii.encrypt("432187652109")
        assert "432187652109" not in token
        assert "4321" not in token

    def test_same_number_encrypts_differently_each_time(self):
        """Fernet includes a random IV, so a repeated number is not detectable
        by comparing ciphertexts."""
        assert pii.encrypt("432187652109") != pii.encrypt("432187652109")

    def test_tampered_ciphertext_is_rejected(self):
        token = pii.encrypt("432187652109")
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        with pytest.raises(pii.PiiKeyError):
            pii.decrypt(tampered)

    def test_garbage_is_rejected(self):
        with pytest.raises(pii.PiiKeyError):
            pii.decrypt("not-a-fernet-token")


class TestNormalise:
    @pytest.mark.parametrize(
        "raw",
        ["4321 8765 2109", "4321-8765-2109", "432187652109", " 4321  8765 2109 "],
    )
    def test_formatting_is_stripped(self, raw):
        assert pii.normalise(raw) == "432187652109"

    def test_case_is_folded(self):
        assert pii.normalise("abcde1234f") == "ABCDE1234F"


class TestMasking:
    def test_last_four_stay_visible(self):
        assert pii.mask("4321 8765 2109") == "XXXX XXXX 2109"

    def test_grouped_in_fours(self):
        """Aadhaar is printed in groups of four; a mask an admin cannot visually
        match against the physical card is no use to them."""
        assert pii.mask("432187652109") == "XXXX XXXX 2109"

    @pytest.mark.parametrize(
        "number,expected",
        [
            ("4321 8765 2109", "XXXX XXXX 2109"),  # Aadhaar, 12
            ("ABCDE1234F", "XXXX XX 234F"),        # PAN, 10
            ("P1234567", "XXXX 4567"),             # passport, 8
        ],
    )
    def test_documents_of_other_lengths(self, number, expected):
        assert pii.mask(number) == expected

    def test_the_visible_tail_is_never_split_across_groups(self):
        """Grouping the whole string in fours would render a PAN as
        "XXXX XX23 4F", straddling the characters an admin is matching against
        the physical card."""
        for number in ("ABCDE1234F", "P1234567", "AB1220230012345"):
            assert pii.mask(number).split()[-1] == pii.last4(number)

    def test_very_short_values_are_not_padded_into_a_lie(self):
        assert pii.mask("1234") == "1234"
        assert pii.mask("12") == "12"

    def test_formatting_does_not_change_the_mask(self):
        assert pii.mask("4321-8765-2109") == pii.mask("4321 8765 2109")

    def test_last4(self):
        assert pii.last4("4321 8765 2109") == "2109"
        assert pii.last4("12") == "12"


class TestFingerprint:
    def test_stable_across_formatting(self):
        assert pii.fingerprint("4321 8765 2109") == pii.fingerprint("4321-8765-2109")
        assert pii.fingerprint("4321 8765 2109") == pii.fingerprint("432187652109")

    def test_different_numbers_differ(self):
        assert pii.fingerprint("432187652109") != pii.fingerprint("432187652108")

    def test_does_not_leak_the_number(self):
        marker = pii.fingerprint("432187652109")
        assert "432187652109" not in marker
        assert len(marker) == 64

    def test_case_insensitive_for_alphanumeric_documents(self):
        assert pii.fingerprint("abcde1234f") == pii.fingerprint("ABCDE1234F")
