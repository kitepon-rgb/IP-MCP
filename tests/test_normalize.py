import pytest

from ip_mcp.jpo.normalize import (
    convert_wareki_year,
    normalize_application_number,
    normalize_publication_number,
    normalize_registration_number,
    parse_identifier,
)


class TestNormalizers:
    def test_publication_number_accepts_10_digits(self):
        assert normalize_publication_number("2010228687") == "2010228687"

    def test_publication_number_strips_hyphens(self):
        assert normalize_publication_number("2010-228687") == "2010228687"

    def test_publication_number_rejects_short_input(self):
        with pytest.raises(ValueError):
            normalize_publication_number("12345")

    def test_application_number_accepts_10_digits(self):
        assert normalize_application_number("2017204947") == "2017204947"

    def test_registration_number_min_4_digits(self):
        assert normalize_registration_number("5094774") == "5094774"
        with pytest.raises(ValueError):
            normalize_registration_number("123")

    def test_fullwidth_normalized_to_halfwidth(self):
        # Full-width digits via NFKC
        assert normalize_publication_number("２０１０－２２８６８７") == "2010228687"


class TestWarekiConversion:
    def test_reiwa_year_1_is_2019(self):
        assert convert_wareki_year("令和", 1) == 2019

    def test_heisei_year_30_is_2018(self):
        assert convert_wareki_year("平成", 30) == 2018

    def test_unknown_era_raises(self):
        with pytest.raises(ValueError):
            convert_wareki_year("元禄", 1)


class TestParseIdentifier:
    def test_parse_publication_with_tokukai_prefix(self):
        kind, number = parse_identifier("特開2010-228687")
        assert kind == "publication"
        assert number == "2010228687"

    def test_parse_application_with_tokugan_prefix(self):
        kind, number = parse_identifier("特願2017-204947")
        assert kind == "application"
        assert number == "2017204947"

    def test_parse_jp_dash_format(self):
        kind, number = parse_identifier("JP-2025-173545")
        assert kind == "publication"
        assert number == "2025173545"

    def test_parse_bare_10_digits_is_application(self):
        kind, number = parse_identifier("2017204947")
        assert kind == "application"
        assert number == "2017204947"

    def test_parse_short_number_is_registration(self):
        kind, number = parse_identifier("5094774")
        assert kind == "registration"
        assert number == "5094774"

    def test_parse_application_with_reiwa(self):
        kind, number = parse_identifier("特願令和3-068946")
        assert kind == "application"
        assert number == "2021068946"

    def test_unparseable_raises(self):
        with pytest.raises(ValueError):
            parse_identifier("not a patent number")
