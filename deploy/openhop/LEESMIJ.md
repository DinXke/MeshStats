# openHop naast MeshManager

[openHop](https://github.com/openhop-dev) is een Python-herimplementatie van
MeshCore: zelfde protocol, zelfde mesh. De repeater-daemon draait op Linux en
praat met zijn radio over een klein binair protocol — over USB, of over TCP op
poort 5055.

Deze map bevat wat er nodig is om hem **naast** deze vloot te draaien, met de
bestaande nodes als antenne. Niet in plaats van: hun eigen modemfirmware maakt
van een node een domme radio en veegt alles weg wat die node verder is.

## De opzet die hier draait

```
              ┌─────────────────────────┐
              │  LXC "openhop"          │
              │  openhop_repeater       │   dashboard op :8000
              │  radios: dak + bureau   │
              └───┬──────────────┬──────┘
         TCP 5055 │              │ TCP 5055
     ┌────────────┴───┐    ┌─────┴──────────────┐
     │ dakrepeater    │    │ MeshUptime-node    │
     │ MeshManagerNet │    │ RoomMesh           │
     │ + openHop-brug │    │ + openHop-brug     │
     └────────────────┘    └────────────────────┘
```

Beide nodes blijven volwaardige MeshCore-nodes; de brug in hun firmware laat
openHop meerijden op hun radio. Zie
[MeshManagerNet](https://github.com/DinXke/MeshManagerNet) en
[MeshUptime](https://github.com/DinXke/MeshUptime) voor die kant.

## Installeren (Debian 13 LXC, unprivileged)

De meegeleverde `manage.sh install` eist een echte terminal; in een
niet-interactieve sessie stopt hij met *"This script requires an interactive
terminal"*. Handmatig werkt prima en is beter te herhalen:

```bash
apt-get install -y git python3-venv python3-pip
git clone https://github.com/openhop-dev/openhop_repeater.git /root/openhop_repeater
python3 -m venv /opt/openhop-venv
/opt/openhop-venv/bin/pip install -e /root/openhop_repeater

mkdir -p /etc/openhop_repeater /var/log/openhop_repeater /var/lib/openhop_repeater
cp config.yaml.voorbeeld /etc/openhop_repeater/config.yaml
$EDITOR /etc/openhop_repeater/config.yaml      # wachtwoorden en JWT-sleutel invullen

cp openhop-repeater.service openhop-plugin-manager.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now openhop-repeater openhop-plugin-manager
```

Twee dingen die je anders zelf moet uitzoeken:

- **`http.host` moet `0.0.0.0` zijn**, niet `''`. CherryPy weigert de lege
  string met *"The empty string ('') is not an allowed value"*, en dan start de
  webserver niet terwijl de radio wél draait.
- **De plugin-manager verwacht `/opt/openhop_repeater/venv`.** Bij een
  handmatige installatie bestaat dat pad niet en meldt het dashboard *"Plugin
  manager is unavailable"*. Oplossing: `ln -sfn /opt/openhop-venv
  /opt/openhop_repeater/venv`, en in de unit de `User=repeater` weghalen als je
  geen aparte servicegebruiker aanmaakt.

## De nodes als antenne

Zet in `config.yaml` per kop een `radios[]`-ingang; de luchtinstellingen
bovenaan (`radio:`) erven ze allebei, wat klopt omdat ze in hetzelfde mesh
staan:

```yaml
radios:
  - id: dak
    radio_type: modem_tcp
    modem_tcp: {host: 192.168.110.178, port: 5055, token: '', lbt_enabled: true}
  - id: bureau
    radio_type: modem_tcp
    modem_tcp: {host: 192.168.110.160, port: 5055, token: '', lbt_enabled: true}
fabric:
  default_radio: dak
  tx_mode: bridge
```

`tx_mode` kent drie standen, en het verschil is de hele feature:

| | |
|---|---|
| `default` | altijd zenden via `default_radio` |
| `sticky` | zenden via de kop die als laatste ontving (antwoord op dezelfde plek) |
| `bridge` | zenden via de ándere kop: wat het dak hoort gaat binnen de lucht in, en omgekeerd |

`bridge` werkt deterministisch bij **precies twee** koppen (`A→B`, `B→A`). Met
drie of meer kiest hij "de eerste andere" — er is geen stand die op álle koppen
tegelijk zendt; `tx_mode: all` wordt expliciet geweigerd.

## De ene keuze die je bewust moet maken

`repeater.mode` bepaalt of openHop zelf doorstuurt:

- **`monitor`** — hij luistert, toont en kan zenden, maar repeteert niet. De
  nodes blijven zelf de repeaters. Niets in het mesh hangt aan deze container.
- **`forward`** — hij is de repeater. Zet dan `repeat off` op de nodes, anders
  gaat elk floodpakket twee keer de lucht in vanaf dezelfde antenne. Gemeten in
  die situatie: vijftien dubbele doorstuuracties in een half uur, van 0,3 tot
  1,3 seconde zendtijd per stuk.

Kies je `forward`, zet dan de **failover** aan op de nodes (`openhop failover on`
respectievelijk de openhop-tab). Die neemt het repeteren terug zodra deze
container wegvalt — en dat hoort op de node te draaien, want als openHop wegvalt
is er vaak méér weg.

Let op: het dashboard schrijft `mode` zelf naar `config.yaml`. Een klik daar
overschrijft wat je in het bestand zette.

## Wat je erbij wil zetten

Een container die de repeater van je gebouw is, hoort bewaakt te worden. De
stilte van deze daemon is precies het soort storing dat je pas ontdekt als er
iets anders misgaat — zet er een uptime-check op (`http://<ip>:8000`) met een
melding die je werkelijk bereikt.
