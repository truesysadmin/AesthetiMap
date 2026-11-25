import pytest
from pydantic import ValidationError

from backend.main import GenerateRequest


def request(**updates):
    return GenerateRequest(city="Kyiv", country="Ukraine", **updates)


def test_defaults_and_extra_fields():
    model = request(ignored="value")
    assert model.theme == "terracotta"
    assert model.span == 20000
    assert (model.width, model.height) == (12, 16)
    assert not model.show_heart
    assert model.format == "png"
    assert model.text_position == "bottom"
    assert "ignored" not in model.model_dump()


@pytest.mark.parametrize("field", ["city", "country", "display_city", "display_country", "country_label"])
@pytest.mark.parametrize("value", ["x" * 81, "bad\x00name", "bad\nname", "bad\x7fname"])
def test_text_rejected(field, value):
    data = {"city": "Kyiv", "country": "Ukraine", field: value}
    with pytest.raises(ValidationError):
        GenerateRequest(**data)


@pytest.mark.parametrize("field", ["city", "country", "display_city", "display_country", "country_label"])
def test_text_trimmed(field):
    data = {"city": "Kyiv", "country": "Ukraine", field: "  Place  "}
    assert getattr(GenerateRequest(**data), field) == "Place"


@pytest.mark.parametrize("field", ["display_city", "display_country", "country_label"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_optional_text_empty(field, value):
    assert getattr(request(**{field: value}), field) is None


@pytest.mark.parametrize("field", ["city", "country"])
def test_required_text_empty(field):
    with pytest.raises(ValidationError):
        GenerateRequest(**{"city": "Kyiv", "country": "Ukraine", field: " "})


@pytest.mark.parametrize("field,value", [
    ("span", 499), ("span", 50001), ("span", 501.5),
    ("width", 1.9), ("height", 40.1), ("width", float("nan")),
    ("height", float("inf")), ("format", "jpeg"), ("format", "PNG"),
    ("text_position", "left"), ("theme", "../terracotta"), ("theme", "missing-theme"),
    ("latitude", "12"), ("longitude", "15"),
])
def test_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize("latitude,longitude", [
    ("86", "0"), ("-86", "0"), ("0", "181"), ("0", "-181"),
    ("not-coordinates", "0"), ("0", "x" * 41), ("nan", "0"),
])
def test_invalid_coordinate_pair(latitude, longitude):
    with pytest.raises(ValidationError):
        request(latitude=latitude, longitude=longitude)


@pytest.mark.parametrize("latitude,longitude", [("85", "180"), ("-85", "-180"), ("50°27′N", "30°31′E")])
def test_valid_coordinates_and_empty_country(latitude, longitude):
    model = GenerateRequest(city="Custom", country="", latitude=latitude, longitude=longitude)
    assert model.country == ""


@pytest.mark.parametrize("span,width,height,format,text_position", [
    (500, 2, 40, "svg", "top"), (50000, 40, 2, "pdf", "center"),
])
def test_valid_boundaries(span, width, height, format, text_position):
    assert request(span=span, width=width, height=height, format=format, text_position=text_position)
