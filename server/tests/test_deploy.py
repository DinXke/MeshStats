"""De invarianten van de gepoorte uitrol (scripts/deploy.sh + release-workflow).

Waarom dit bestand bestaat. In de testsuite draait geen Docker en geen git, net
zomin als er Compose draait bij test_compose.py -- en om dezelfde reden bewaken
we de eigenschappen van dit script dan maar met het blote lezen ervan. Het gaat
om eigenschappen die je niet met het oog ziet maar die het verschil zijn tussen
een veilige uitrol en de blinde deploy die twee storingen kostte:

  * eerst bouwen, dan pas :latest verzetten (een gefaalde build wisselt niets);
  * de draaiende image als :previous bewaren VOOR de wissel (de weg terug);
  * de functionele poort bevraagt de kern-tabellen die ook echt in het schema
    staan -- lopen die twee lijsten uiteen, dan vangt de poort niets meer;
  * geen kale 'docker compose up --build' meer, want dat IS de blinde deploy;
  * de exitcodes die de docs beloven, staan ook echt in het script.

Weg te halen zou betekenen dat een herschrijving van deploy.sh die stilletjes
eerst naar :latest bouwt, of de rollback laat vallen, ongemerkt langskomt.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY = ROOT / "scripts" / "deploy.sh"
COMPOSE = ROOT / "docker-compose.yml"
DB = ROOT / "server" / "app" / "db.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


# --- het bestaan en de vorm --------------------------------------------------

def test_deploy_script_bestaat_met_shebang():
    assert DEPLOY.exists(), "scripts/deploy.sh ontbreekt"
    assert _deploy().startswith("#!"), "deploy.sh mist een shebang"


def test_geen_set_e_want_de_rollback_moet_na_een_fout_nog_draaien():
    # 'set -e' zou het script laten stoppen zodra de poort faalt -- nog voor de
    # rollback. De fouten worden met de hand afgevangen; -u mag, -e niet.
    tekst = _deploy()
    assert re.search(r"^set -u\b", tekst, re.M), "deploy.sh hoort 'set -u' te gebruiken"
    assert not re.search(r"^set -e", tekst, re.M), (
        "deploy.sh gebruikt 'set -e'; dan draait de rollback niet meer nadat de "
        "poort faalde")


# --- de kernvolgorde: bouwen -> :previous -> :latest -------------------------

def test_eerst_bouwen_dan_pas_latest_verzetten():
    """De veiligheidsinvariant. Een gefaalde build mag niets wisselen, dus de
    build (naar een eigen tag) moet VOOR het verzetten van :latest staan."""
    tekst = _deploy()
    build = tekst.find('docker build -t "$IMAGE:$IMG_TAG"')
    swap = tekst.find('docker tag "$IMAGE:$IMG_TAG" "$IMAGE:latest"')
    assert build != -1, "geen build naar een eigen tag ($IMAGE:$IMG_TAG) gevonden"
    assert swap != -1, "geen wissel van :latest naar de nieuwe build gevonden"
    assert build < swap, "de build moet VOOR het verzetten van :latest staan"


def test_previous_wordt_bewaard_tussen_bouwen_en_wisselen():
    """De weg terug. De draaiende image wordt als :previous getagd ná een
    geslaagde build en VOOR de wissel, zodat :previous exact is wat er draaide."""
    tekst = _deploy()
    build = tekst.find('docker build -t "$IMAGE:$IMG_TAG"')
    prev = tekst.find('"$IMAGE:previous"')
    swap = tekst.find('docker tag "$IMAGE:$IMG_TAG" "$IMAGE:latest"')
    assert prev != -1, "de draaiende image wordt nergens als :previous bewaard"
    assert build < prev < swap, (
        "het bewaren van :previous hoort tussen de build en de wissel te staan")


def test_previous_wordt_op_de_image_id_vastgelegd_niet_op_een_tag():
    # Tags schuiven, een id niet. De rollback moet exact terug naar wat er
    # draaide, dus wordt de id van de draaiende container getagd.
    assert 'docker inspect -f' in _deploy() and '.Image' in _deploy(), (
        "deploy.sh legt :previous niet vast op de image-id van de container")


# --- de rollback -------------------------------------------------------------

def test_rollback_zet_latest_terug_op_previous_en_start_opnieuw():
    tekst = _deploy()
    assert 'docker tag "$IMAGE:previous" "$IMAGE:latest"' in tekst, (
        "de rollback zet :latest niet terug naar :previous")
    # Ná het terugzetten hoort de container opnieuw omhoog te komen.
    assert "docker compose up -d" in tekst, "de rollback herstart de container niet"


def test_de_beloofde_exitcodes_staan_in_het_script():
    # De docs (docs/deploy.md) beloven 2/3/4 voor build-fout/rollback/rollback-fout.
    # Die belofte moet het script waarmaken.
    tekst = _deploy()
    for code in ("fail 2 ", "fail 3 ", "fail 4 "):
        assert code in tekst, f"exitcode-tak ontbreekt: {code.strip()}"


# --- de blinde deploy mag niet terugkomen ------------------------------------

def test_geen_kale_up_build_in_de_deploy():
    """'docker compose up -d --build' IS de blinde deploy die dit vervangt: hij
    bouwt en wisselt in één adem, zonder gebouwde-tag om op terug te vallen.

    Alleen echte commandoregels tellen -- het woord --build in de toelichting
    (juist over waarom we het NIET doen) is geen overtreding."""
    code_regels = [r for r in _deploy().splitlines() if not r.lstrip().startswith("#")]
    overtreders = [r for r in code_regels if re.search(r"\bup\b.*--build", r)]
    assert not overtreders, (
        "deploy.sh gebruikt 'up --build'; dat is precies de blinde wissel die de "
        "gebouwde-tag-aanpak vervangt: " + " | ".join(overtreders))


# --- de functionele poort bevraagt de tabellen die echt bestaan --------------

def _core_tabellen_uit_deploy() -> list[str]:
    tekst = _deploy()
    m = re.search(r"CORE\s*=\s*\[(.*?)\]", tekst, re.S)
    assert m, "geen CORE-lijst met kern-tabellen in deploy.sh gevonden"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def test_de_functionele_query_draait_in_de_app_container():
    tekst = _deploy()
    assert 'docker exec' in tekst and '"$APP_CONTAINER" python -' in tekst, (
        "de functionele query hoort ín de app-container te draaien (docker exec)")


def test_kern_tabellen_van_de_poort_bestaan_ook_echt_in_het_schema():
    """Als de poort een tabel bevraagt die niet meer bestaat, faalt elke deploy;
    verdwijnt een tabel stil uit het schema zonder uit CORE te gaan, dan vangt de
    poort niets meer. Deze test houdt de twee lijsten aan elkaar."""
    schema = DB.read_text(encoding="utf-8")
    aanwezig = set(re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+)", schema))
    core = _core_tabellen_uit_deploy()
    assert core, "CORE-lijst is leeg"
    ontbreekt = [t for t in core if t not in aanwezig]
    assert not ontbreekt, (
        "de poort bevraagt tabellen die niet in db.py's schema staan: "
        + ", ".join(ontbreekt))


# --- overeenstemming met docker-compose.yml ----------------------------------

def test_container_en_image_naam_komen_overeen_met_compose():
    """De standaardwaarden in deploy.sh moeten de namen zijn die Compose zet,
    anders praat het script tegen een container die niet bestaat."""
    compose = COMPOSE.read_text(encoding="utf-8")
    tekst = _deploy()
    assert "container_name: meshmanager" in compose
    assert "image: meshmanager:latest" in compose
    assert 'APP_CONTAINER="${APP_CONTAINER:-meshmanager}"' in tekst, (
        "deploy.sh's APP_CONTAINER wijkt af van compose's container_name")
    assert 'IMAGE="${IMAGE:-meshmanager}"' in tekst, (
        "deploy.sh's IMAGE wijkt af van compose's image-naam")


# --- de release-workflow -----------------------------------------------------

def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_draait_op_v_tags():
    assert WORKFLOW.exists(), ".github/workflows/release.yml ontbreekt"
    tekst = _workflow()
    assert "'v*'" in tekst, "de release-workflow triggert niet op tags v*"


def test_workflow_draait_de_tests_en_bouwt_het_image():
    tekst = _workflow()
    assert "pytest" in tekst, "de release-workflow draait de servertests niet"
    assert "docker build" in tekst, "de release-workflow bouwt het image niet"


def test_workflow_python_gelijk_aan_de_dockerfile():
    # De tests horen op de Python te draaien waarop de container straks draait.
    dockerfile = (ROOT / "server" / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12" in dockerfile
    assert "python-version: '3.12'" in _workflow(), (
        "de workflow test op een andere Python dan de Dockerfile gebruikt")
