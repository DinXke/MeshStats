#pragma once

/* MeshStatsNet — geeft een repeater een IP-leven naast zijn mesh-leven:
 * WiFi, een beheerpagina, firmware-upgrades en een console.
 *
 * Uitgangspunt bij alles hieronder: deze repeater hangt op een dak. Hij mag
 * nooit onbereikbaar worden. Daarom:
 *
 *  - WiFi lukt niet?          hij zendt zijn eigen SSID uit met dezelfde
 *                             beheerpagina, en blijft je netwerk opnieuw
 *                             proberen (zelfherstellend)
 *  - beheerpagina stuk?       de mesh-CLI blijft werken (wifi-commando's)
 *  - mijn code crasht?        een bootteller start hem na 3 herstarts in
 *                             veilige modus: mesh + AP + pagina, verder niets
 *  - radio-init faalt?        we blijven niet eeuwig hangen zoals de
 *                             standaardfirmware, maar starten het netwerkdeel
 *                             tóch, zodat je kan herflashen
 *  - upgraden?                /update op de gewone beheerpagina, dus over je
 *                             eigen WiFi en niet enkel via de OTA-softAP
 *
 * De webserver is asynchroon (AsyncWebServer). Een blokkerende server houdt de
 * hoofdlus op, en daarmee de mesh — dat gedrag hebben we op de companion-node
 * al gezien.
 */

#include <Arduino.h>
#include <FS.h>

class MyMesh;

// Aan te roepen in setup(), na het bestandssysteem en de mesh.
void msnet_begin(FS &fs, MyMesh *mesh);

// Aan te roepen in loop(). Doet nooit iets dat lang blokkeert.
void msnet_loop();

/* Vangt de wifi-commando's af. Geeft true als het commando van ons was.
 * Wordt aangeroepen vanuit zowel de seriële CLI, de mesh-CLI als de console,
 * zodat je met een kapotte WiFi-configuratie via de mesh binnen raakt:
 *
 *   wifi                 toestand, IP, signaal
 *   wifi ssid <naam>     netwerk instellen (leeg = ingebouwde waarde)
 *   wifi pass <woord>    wachtwoord instellen
 *   wifi connect         opnieuw verbinden met de ingestelde gegevens
 *   wifi ap              nu het eigen netwerk uitzenden
 *   wifi console <user> <pass>   inloggegevens van de console
 */
bool msnet_handle_command(const char *command, char *reply);

// True als de node in veilige modus draait (na herhaalde herstarts).
bool msnet_is_safe_mode();
