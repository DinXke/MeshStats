"""Testopzet die vóór elke app-import moet gebeuren.

Het importeren van ``app.config`` maakt de datamap aan en schrijft er een
geheime sleutel in. Zonder deze omleiding zou de eerste testrun dus een
``server/data/`` met een ``secret.key`` in de werkkopie achterlaten. Daarom
wijst MCS_DATA_DIR hier naar een wegwerpmap, en wel op module-niveau: conftest
wordt door pytest geladen voordat enige testmodule ``app`` importeert.
"""
import os
import tempfile

os.environ.setdefault("MCS_DATA_DIR",
                      tempfile.mkdtemp(prefix="meshstats-test-data-"))
