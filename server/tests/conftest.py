"""Testopzet die vóór elke app-import moet gebeuren.

Het importeren van ``app.config`` maakt de datamap aan en schrijft er een
geheime sleutel in. Zonder deze omleiding zou de eerste testrun dus een
``server/data/`` met een ``secret.key`` in de werkkopie achterlaten. Daarom
wijst MM_DATA_DIR hier naar een wegwerpmap, en wel op module-niveau: conftest
wordt door pytest geladen voordat enige testmodule ``app`` importeert.

De oude naam (``MCS_DATA_DIR``) wordt hier met opzet NIET gebruikt: dat de
terugval werkt, hoort een test te zijn die dat expliciet zegt, niet iets wat de
hele suite toevallig meebewijst. Zie test_naamswissel.py.
"""
import os
import tempfile

os.environ.setdefault("MM_DATA_DIR",
                      tempfile.mkdtemp(prefix="meshmanager-test-data-"))
