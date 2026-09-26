from textwrap import dedent

import pytest

from conftest import MINIMAL_SCENARIO
from harness import ScenarioError, load_scenario

VALID = """\
seed: 7
fleet:
  nominal_mw: 80
products:
  ECRS: {cap_mw: 100, cap_share: 0.9}
  NONSPIN: {cap_mw: 100, cap_share: 0.9}
"""


def write(tmp_path, text, name="custom.yaml"):
    path = tmp_path / name
    path.write_text(dedent(text))
    return path


def test_the_minimal_scenario_loads():
    sc = load_scenario(MINIMAL_SCENARIO)

    assert sc.name == "minimal"
    assert set(sc.products) == {"ECRS", "NONSPIN"}
    assert sc.products["ECRS"].award_limit_mw == pytest.approx(90.0)


def test_a_scenario_is_named_after_its_file(tmp_path):
    sc = load_scenario(write(tmp_path, VALID, name="calm_week.yaml"))

    assert (sc.name, sc.seed, sc.fleet.nominal_mw) == ("calm_week", 7, 80.0)


@pytest.mark.parametrize("text, error", [
    (VALID.replace("  nominal_mw: 80", "  nominal_mw: 80\n  homes: 1000"),
     "fleet: unknown field 'homes'"),
    (VALID.replace("seed: 7\n", "seed: 7\nfailures: {}\n"),
     "unknown field 'failures'"),
    (VALID.replace("{cap_mw: 100, cap_share: 0.9}\n", "{cap_mw: 100}\n", 1),
     "products.ECRS: missing field 'cap_share'"),
    (VALID.replace("seed: 7\n", ""),
     "missing field 'seed'"),
    (VALID.replace("  NONSPIN:", "  RRS:"),
     "products: unknown field 'RRS'"),
    (VALID.replace("nominal_mw: 80", "nominal_mw: lots"),
     "fleet.nominal_mw: expected a number, got 'lots'"),
    (VALID.replace("nominal_mw: 80", "nominal_mw: .nan"),
     "fleet.nominal_mw: expected a number, got nan"),
    (VALID.replace("seed: 7", "seed: 7.5"),
     "seed: expected a non-negative integer, got 7.5"),
    (VALID.replace("cap_share: 0.9}", "cap_share: 1.5}", 1),
     "products.ECRS.cap_share: must be between 0 and 1, got 1.5"),
    (VALID.replace("nominal_mw: 80", "nominal_mw: -5"),
     "fleet.nominal_mw: must be at least 0, got -5"),
    ("- just\n- a list\n",
     "expected a mapping"),
    (VALID + "seed: 8\n",
     "duplicate field 'seed'"),
])
def test_invalid_scenarios_give_a_clear_error(tmp_path, text, error):
    path = write(tmp_path, text)

    with pytest.raises(ScenarioError) as raised:
        load_scenario(path)

    assert error in str(raised.value)
    assert str(path) in str(raised.value)


def test_a_missing_scenario_file_gives_a_clear_error(tmp_path):
    with pytest.raises(ScenarioError, match="nowhere.yaml: cannot read"):
        load_scenario(tmp_path / "nowhere.yaml")
