#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maakt StatsPage.h uit page.html.

De beheerpagina gaat gzip-gecomprimeerd de lucht in. Reden: lwip heeft op deze
build een socket-verzendbuffer van 5760 bytes, en WiFiClient::write() geeft na
tien mislukte pogingen een gedeeltelijk aantal bytes terug zonder dat de
webserver daarnaar kijkt. Past het antwoord niet in een keer, dan staat er wel
een Content-Length in de kop die nooit gehaald wordt en blijft de browser
wachten. Ongecomprimeerd is de pagina daar te groot voor; gzip brengt haar ruim
onder die grens.

Gebruik:
    python examples/companion_radio/gen_page.py

Pas altijd page.html aan en draai daarna dit script. StatsPage.h is
gegenereerd; bewerk die niet met de hand.
"""
import gzip, io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'page.html')
DST = os.path.join(HERE, 'StatsPage.h')

# Grens waar het om begon: CONFIG_LWIP_TCP_SND_BUF_DEFAULT op deze build.
SND_BUF = 5760

raw = io.open(SRC, 'rb').read()

# mtime=0 zodat hetzelfde page.html altijd dezelfde blob geeft en een
# regeneratie zonder inhoudelijke wijziging geen diff oplevert.
buf = io.BytesIO()
with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=9, mtime=0) as f:
    f.write(raw)
blob = buf.getvalue()

lines = []
for i in range(0, len(blob), 16):
    chunk = blob[i:i + 16]
    lines.append('  ' + ' '.join('0x%02x,' % b for b in chunk))

out = '''#pragma once

/* GEGENEREERD BESTAND - niet met de hand bewerken.
 *
 * Bron:     examples/companion_radio/page.html
 * Opnieuw:  python examples/companion_radio/gen_page.py
 *
 * De beheerpagina wordt gzip-gecomprimeerd verstuurd. lwip heeft hier een
 * socket-verzendbuffer van %d bytes, en WiFiClient::write() geeft na tien
 * mislukte pogingen een gedeeltelijk aantal bytes terug zonder dat WebServer
 * daarnaar kijkt: dan belooft de kop een Content-Length die nooit gehaald wordt
 * en blijft de browser hangen. Ongecomprimeerd paste de pagina daar niet in.
 *
 * Onberoerd: %d bytes
 * Gzip:      %d bytes (%.0f%% van het origineel)
 */

#include <Arduino.h>

static const uint8_t PAGE_GZ[] PROGMEM = {
%s
};

static const size_t PAGE_GZ_LEN = sizeof(PAGE_GZ);
''' % (SND_BUF, len(raw), len(blob), 100.0 * len(blob) / len(raw), '\n'.join(lines))

io.open(DST, 'w', encoding='utf-8', newline='\n').write(out)

print('page.html : %5d bytes' % len(raw))
print('StatsPage.h: %5d bytes gzip (%.0f%% van het origineel)'
      % (len(blob), 100.0 * len(blob) / len(raw)))
if len(blob) >= SND_BUF:
    print('LET OP: %d bytes haalt de verzendbuffer van %d niet; de pagina zal '
          'opnieuw in stukken moeten.' % (len(blob), SND_BUF))
    sys.exit(1)
print('ruim binnen de verzendbuffer van %d bytes.' % SND_BUF)
