# KVR OSC Data Platform v1 - TODO

## Phase 0 – Bestandsaufnahme

- [ ] Aktuelle CSV-Dateien inventarisieren
- [ ] Extractor dokumentieren
- [ ] Hall-of-Fame Generator dokumentieren
- [ ] Archive.org Zuordnungen analysieren
- [ ] Bekannte Fehlerliste erstellen

## Phase 1 – Datenbasis stabilisieren

- [ ] osc_master.csv definieren
- [ ] artists.csv definieren
- [ ] archive_links.csv definieren
- [ ] scorecard_index.csv definieren
- [ ] import_batches.csv definieren
- [ ] artist_id Konzept definieren
- [ ] Alias-Konzept definieren
- [ ] Datenmodell dokumentieren

## Phase 2 – Importsystem

- [ ] Scorecard Index aufbauen
- [ ] SHA256 Hash-Erkennung einbauen
- [ ] Neu-/Update-Erkennung
- [ ] staging_import.csv einführen
- [ ] Diff-System entwickeln
- [ ] Änderungsreport erzeugen
- [ ] Reimport Workflow definieren

## Phase 3 – Datenqualität

- [ ] Duplicate Rank Check
- [ ] Rank Gap Check
- [ ] Unknown Artist Check
- [ ] Alias Candidate Check
- [ ] Missing Archive Check
- [ ] Archive Mismatch Check
- [ ] Artist/Track Swap Check
- [ ] issues.csv erzeugen
- [ ] issues.json erzeugen

## Phase 4 – Artist Registry

- [ ] Artist IDs vergeben
- [ ] Alias-Verwaltung einführen
- [ ] Artist Merge Funktion
- [ ] Artist Historie erzeugen
- [ ] artist_stats.csv generieren
- [ ] Statistiken aus Masterdaten berechnen

## Phase 5 – Archive.org Integration

- [ ] Archive Identifier speichern
- [ ] Archive Crawler integrieren
- [ ] Tracklisten importieren
- [ ] Match Engine verbessern
- [ ] Match Status einführen
- [ ] Match Score einführen
- [ ] Review Workflow entwickeln

## Phase 6 – Admin Backend

- [ ] Login.php
- [ ] Logout.php
- [ ] Session Handling
- [ ] API Layer
- [ ] OSC Editor
- [ ] Artist Editor
- [ ] Änderungsprotokoll
- [ ] Automatische Backups
- [ ] Backup ZIP Export
- [ ] Backup Restore
- [ ] Backup Notizen

## Phase 7 – Hall of Fame v2

- [ ] Hall of Fame auf Masterdaten umstellen
- [ ] Artists Explorer
- [ ] Artist Detail View
- [ ] OSC Browser
- [ ] Results Explorer
- [ ] Archive Player Integration

## Phase 8 – Wartungsbetrieb

- [ ] Scorecard Upload
- [ ] Import Vorschau
- [ ] Diff Anzeige
- [ ] Änderungen übernehmen
- [ ] Rebuild Workflow
- [ ] Backup vor Import

## Phase 9 – User & Security

- [ ] users.csv definieren
- [ ] Rollenmodell (admin/editor/viewer)
- [ ] Passwort Hashing
- [ ] Benutzerverwaltung
- [ ] Login Protokoll
- [ ] CSRF Schutz
- [ ] Schreibrechte je Rolle
- [ ] Optional: 2FA per Mail-Code

## Phase 10 – Statistik & Analytics

- [ ] analytics_events.csv definieren
- [ ] Player Klicks erfassen
- [ ] OSC Klicks erfassen
- [ ] Filter Nutzung erfassen
- [ ] Suchanfragen erfassen
- [ ] Analytics Dashboard
- [ ] CSV Export

## Phase 11 – Player & Discovery Features

- [ ] Random Track Vorauswahl
- [ ] Kein automatisches Play
- [ ] Nur verifizierte Archive Tracks
- [ ] Random Track Button
- [ ] Discovery Features
- [ ] Artist Discovery
- [ ] OSC Discovery

## Phase 12 – Deployment & Backup

- [ ] Deployment Dokumentation
- [ ] Backup Strategie dokumentieren
- [ ] Restore testen
- [ ] Rollback Strategie
- [ ] Langzeitarchivierung
