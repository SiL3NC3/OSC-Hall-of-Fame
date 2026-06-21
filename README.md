# KVR OSC Cockpit

Cockpit und Analysewerkzeuge für die KVR One Synth Challenge (OSC).

## Funktionen

- Verwaltung der Challenge-Liste (`kvrosc_challenges.csv`)
- Analyse von OSC-Einsendungen
- Scorecard-Auswertung
- HTML-Archiv der Challenge-Seiten
- Audio- und Cover-Archiv
- Diverse Hilfsskripte für Datensammlung und Reporting

## Verzeichnisstruktur

```text
dist/                         Build-/Release-Artefakte
dist/tmp/                     Temporärer HOF-Build-Workspace, wird gelöscht
dist/hof/                     Komplettes Hall-of-Fame-Bundle für den Webserver
scripts/                     Automatisierung und Tools
scorecards/                  Alle Scorecards für OSC001-OSC207
scorecard_structure_analysis/ Analyse der Scorecards
osc_analysis/                Generierte Analysen
osc_archive/                 Archivierte Challenge-Daten
osc_audio/                   Audio-Dateien
osc_covers/                  Cover-Bilder
Chat-Logs/                   Projektdokumentation
kvrosc_challenges.csv        Challenge-Datenbank
```

## Voraussetzungen

- Python 3.11+
- requests
- beautifulsoup4
- pandas

Installation:

```bash
pip install -r requirements.txt
```

## Verwendung

Beispiele:

```bash
python scripts/update_challenges.py
python scripts/build_analysis.py
```

### Wichtige Update-Skripte

- `update.sh` aktualisiert das OSC-Cockpit und ist nicht für den Hall-of-Fame-Rebuild gedacht.
- `update_hall-of-fame.sh` baut den Hall-of-Fame-Flow neu auf und schreibt das komplette Release-Bundle nach `dist/hof/`.
- `scripts/scorecard_vote_extractor.py` ist die primäre Datenquelle für Scorecards, unterstützt das komplette `scorecards/`-Verzeichnis und schreibt archive.org-Verifikationsdaten plus Stream-Links in die CSVs.
- Die archive.org-Metadaten werden in `dist/archive-cache/archive_validation_cache.json` zwischengespeichert, damit Rebuilds schneller werden.
- `scripts/kvrosc_hall_of_fame_generator.py` rendert nur die HTML-Seite aus den CSVs und der Extraktor-Ausgabe. Es zieht keine Scorecards mehr direkt nach und baut den archive.org-Player in die Detailansicht ein.

### Versionierung Und Changelog

- Aktuelle Versionsstände sind im Script selbst hinterlegt: `scripts/scorecard_vote_extractor.py` = `v0.14`
- Aktuelle Versionsstände sind im Script selbst hinterlegt: `scripts/kvrosc_hall_of_fame_generator.py` = `v0.20`
- Änderungen werden zusätzlich in [`CHANGELOG.md`](CHANGELOG.md) dokumentiert.
- Wenn du den HOF oder den Extractor änderst, die jeweilige `version`-Variable mit hochziehen.
- Der Changelog folgt dem Keep-a-Changelog-Prinzip: neue Änderungen kommen oben dazu, ältere Releases bleiben darunter erhalten.

### Hall-of-Fame Rebuild

```bash
./update_hall-of-fame.sh
```

Der Ablauf ist:

1. Alle Scorecards kommen aus `scorecards/`.
2. `scorecard_vote_extractor.py` baut in `dist/tmp/` die kompletten CSVs neu.
3. `kvrosc_hall_of_fame_generator.py` rendert gegen `dist/tmp/` die HTML neu.
4. `update_hall-of-fame.sh` kopiert danach nur die Netto-Dateien nach `dist/hof/` und löscht `dist/tmp/`.

Netto-Dateien in `dist/hof/`:

- `index.html`
- `normalized_results.csv`
- optional `.htaccess` und `.passwd`, wenn `scripts/kvrosc_hall_of_fame_config.yaml` den Schutz aktiviert

Alles andere bleibt im Build-Workspace `dist/tmp/` und wird danach gelöscht.

Nicht mehr Teil des HOF-Release-Bundles sind:

- `hall_of_fame_winners.csv`
- `hall_of_fame_podiums.csv`
- `hall_of_fame_streaks.csv`
- `normalized_votes.csv`
- `scorecard_file_inventory.csv`
- `parser_diagnostics.csv`
- `hard_reconciliation_by_osc.csv`
- `hard_reconciliation_details.csv`
- `duplicate_osc_files.csv`
- `scorecard_variant_summary.csv`
- `vote_stats_by_osc.csv`
- `rename_plan_to_template_versions.csv`
- `winners_by_osc.csv`
- `top5_all_osc.csv`
- `artist_medals_top5_raw.csv`
- `artist_medals_top5_canonical.csv`
- `artist_alias_candidates.csv`
- `cockpit_hall_of_fame.csv`
- `cockpit_scorecards_data.json`
- `validation_matrix.csv`
- `validation_failures.csv`
- `voting_rule_history_by_osc.csv`
- `run_summary.json`
- `parser_plan.md`
- `README_probe.md`

### Backup-Kandidaten

Das hier sind die naheliegenden Kandidaten, die du nach `BACKUP/` verschieben kannst, wenn du den aktiven Baum schlank halten willst:

- Alte Hall-of-Fame-HTML-Skripte: `scripts/kvrosc_hall_of_fame_html_v0_2.py` bis `scripts/kvrosc_hall_of_fame_html_v0_14.py`
- Alte Extractor-Versionen: `scripts/scorecard_vote_extractor_v0_4.py`, `scripts/scorecard_vote_extractor_v0_5.py`, `scripts/scorecard_vote_extractor_v0_6.py`
- Alte Cockpit-Versionen: `scripts/kvrosc_OSC-Cockpit_v1_77!!.py`, `scripts/kvrosc_OSC-Cockpit_v1_78.py`, `scripts/kvrosc_OSC-Cockpit_v1_78_old.py`, `scripts/kvrosc_OSC-Cockpit_v1_79.py`, `scripts/kvrosc_OSC-Cockpit_v1_80.py`, `scripts/kvrosc_OSC-Cockpit_v1_81.py`, `scripts/kvrosc_OSC-Cockpit_v1_82.py`
- Ältere Analyse-Helfer, falls du sie nicht mehr aktiv nutzt: `scripts/scorecard_deep_probe_v0_2.py`, `scripts/scorecard_deep_probe_v0_3.py`, `scripts/scorecard_variant_lab_v0_1.py`, `scripts/kvrosc_analyze_scorecard_structures_v0_1.py`, `scripts/kvrosc_analyze_scorecard_structures_v0_2.py`
- Historische HTML-Ausgaben: `KVR_OSC_Hall_of_Fame.html` und die alten `KVR_OSC_Hall_of_Fame_v0_*.html`
- Der frühere separate Audit-Output-Ordner `hall_of_fame_audit_v0_1/` liegt jetzt bereits in `BACKUP/old-code/`, damit der aktive HOF-Flow wirklich nur noch aus zwei Skripten besteht.

Diese Dinge würde ich eher nicht ins Backup schieben:

- `update.sh`, weil es dein Cockpit aktualisiert
- `update_hall-of-fame.sh`, weil es den aktuellen HOF-Rebuild startet
- `scripts/scorecard_vote_extractor.py`
- `scripts/kvrosc_hall_of_fame_generator.py`
- `scripts/kvrosc_hall_of_fame_config.yaml`
- `scorecards/`, weil das der einzige aktive Scorecard-Input ist
- `kvrosc_challenges.csv`, weil das die Challenge-Metadaten enthält
- `dist/hof/`, wenn du die erzeugten Webserver-Dateien weiter als Release-Bundle behalten willst
- `BACKUP/old-code/scorecard_lab/`, weil es nur noch ein alter Arbeits-/Output-Ordner ist

## Projektstatus

Aktueller Stand (Juni 2026):

- Challenge-Datenbank gepflegt
- Analysepipeline funktionsfähig
- Cockpit in aktiver Entwicklung
- Historische OSC-Daten archiviert

## Lizenz

Privates Forschungs- und Hobbyprojekt.
```
