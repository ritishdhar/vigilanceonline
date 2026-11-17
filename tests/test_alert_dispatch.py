from core.alert_dispatch import incident_from_state


def test_incident_from_state():
    assert incident_from_state("NO_FIGHT", {}, 1.0) is None
    assert incident_from_state(None, {}, 1.0) is None
    inc = incident_from_state("GROUP_ASSAULT", {}, 2.5)
    assert inc.title == "Group assault detected" or "Group" in inc.title
    inc2 = incident_from_state(None, {"Fall Detection": {}}, 3.0)
    assert "Fall" in inc2.title
