import pytest
from pydantic import ValidationError

from backend.main import GenerateRequest, Marker, app
from fastapi.testclient import TestClient


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


def test_marker_defaults_and_plain_dicts():
    model = request(markers=[{"type": "heart", "label": "  Our home  "}], connect=True)
    assert model.markers[0] == Marker(type="heart", label="Our home")
    assert model.model_dump()["markers"] == [{
        "type": "heart", "lat": None, "lon": None, "size": 72, "opacity": 1,
        "color": "text", "label": "Our home",
    }]
    assert model.connect is True
    assert request().markers == []


@pytest.mark.parametrize("updates", [
    {"type": "unknown"}, {"type": "../heart"}, {"extra": True},
    {"lat": 0}, {"lon": 0}, {"lat": 86, "lon": 0}, {"lat": 0, "lon": 181},
    {"lat": float("nan"), "lon": 0}, {"lat": 0, "lon": float("inf")},
    {"size": 11.9}, {"size": 500.1}, {"size": float("nan")},
    {"opacity": 0.149}, {"opacity": 1.01}, {"opacity": float("inf")},
    {"color": "red"}, {"color": "#fff"}, {"color": "bg"},
    {"label": "x" * 41}, {"label": "bad\nlabel"}, {"label": "bad\x00label"},
    {"label": "bad\x7flabel"},
])
def test_marker_invalid_fields(updates):
    with pytest.raises(ValidationError):
        request(markers=[{"type": "heart", **updates}])


@pytest.mark.parametrize("color", ["text", "road_motorway", "water", "#C0392B", "#abcdef"])
@pytest.mark.parametrize("lat,lon,size,opacity", [(-85, -180, 12, 0.15), (85, 180, 500, 1)])
def test_marker_boundaries(color, lat, lon, size, opacity):
    assert Marker(type="pin", lat=lat, lon=lon, size=size, opacity=opacity, color=color)


def test_marker_limit_and_legacy_heart():
    markers = [{"type": "dot"}] * 5
    model = request(markers=markers, show_heart=True)
    assert len(model.markers) == 5
    assert model.show_heart
    with pytest.raises(ValidationError):
        request(markers=markers + [{"type": "pin"}])


@pytest.mark.parametrize("markers", [
    [{"type": "heart", "extra": 1}], [{"type": "pin", "lat": 1}],
    [{"type": "heart", "label": "bad\nlabel"}], [{"type": "dot"}] * 6,
])
def test_invalid_marker_api_returns_422(markers):
    response = TestClient(app).post("/api/generate_map_stream", json={
        "city": "Kyiv", "country": "Ukraine", "markers": markers,
    })
    assert response.status_code == 422
