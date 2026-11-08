<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/eflux_road/brand/dark_logo.png">
    <img alt="E-Flux by Road" src="custom_components/eflux_road/brand/logo.png" width="300">
  </picture>
</p>

# E-Flux by Road voor Home Assistant

Een community-integratie; geen officiële integratie van E-Flux by Road.

Start en volg je E-Flux (Road.io) laadsessies rechtstreeks vanuit Home Assistant. Ideaal voor het automatisch starten van laadsessies wanneer je je auto (zoals een Tesla) inplugt.

## Wat het doet

- **Sessie starten op afstand**: Start laadsessies op je gekoppelde E-Flux laadpaal (zoals een Alfen Eve) met je geconfigureerde laadpas.
- **Voorzichtige herhaling**: Een nieuwe poging volgt alleen wanneer E-Flux bevestigt dat het vorige verzoek is afgewezen. Een onzekere uitkomst blokkeert nieuwe verzoeken.
- **Sensoren & Statussen**:
  - `sensor.e_flux_laadpaal_connectorstatus`: Actuele connectorstatus (bijv. `AVAILABLE`, `PREPARING`, `CHARGING`, `OCCUPIED`).
  - `sensor.e_flux_laadpaal_actieve_sessies`: Aantal actieve laadsessies op je account.
  - `sensor.e_flux_laadpaal_laatste_startstatus`: Status van het laatste startverzoek (`STARTING`, `COMPLETED`, `CANCELLED` met foutreden in attributen).
  - `sensor.e_flux_laadpaal_laatste_startverzoek`: Statusmelding van de startflow.
  - Energie, begintijd en laadstatus van de huidige sessie, wanneer Road deze gegevens aan de gekozen sessie koppelt.
  - Gemaskeerde laadpas en passtatus, beschikbaarheid volgens E-Flux en maximaal laadvermogen wanneer bekend.
- **Bediening**:
  - Knop: `button.e_flux_laadpaal_start_laadsessie`
  - Knoppen voor stoppen en status verversen.
  - Acties: `eflux_road.start_session` en `eflux_road.stop_session` (bij één geconfigureerd laadpunt).
- **Flexibele inlogflow**:
  - Direct inloggen met E-Flux e-mailadres en wachtwoord, wanneer E-Flux voor dat account geen aanvullende verificatie vereist.
  - Of een browser-sessie-export: de JWT én het bijbehorende apparaat-token. Dit is de aanbevolen route wanneer E-Flux extra verificatie, 2FA of Cloudflare bot-checks gebruikt.
  - Home Assistant vernieuwt dit paar automatisch als Road daarom vraagt of de JWT bijna verloopt. Beide waarden roteren samen; wachtwoord, 2FA-code en browsercookie worden niet opgeslagen.

---

## Installatie

### Via HACS / Marketplace (Aanbevolen)

1. Open Home Assistant en ga naar **HACS** (of **Marketplace** in HA 2026.11+).
2. Klik rechtsboven op het menu (drie puntjes) en kies **Aangepaste repositories** (of *Toevoegen via link*).
3. Vul de repository-URL in: `https://github.com/gielk/ha-eflux-road` en kies als type **Integratie**.
4. Zoek en download **E-Flux by Road**.
5. Herstart Home Assistant.

### Handmatige installatie

1. Kopieer de map `custom_components/eflux_road` naar de map `custom_components` in je Home Assistant configuratiemap (`/config/custom_components/`).
2. Herstart Home Assistant.

---

## Integratie configureren

1. Ga in Home Assistant naar **Instellingen** → **Apparaten en diensten** → **Integratie toevoegen**.
2. Zoek naar **E-Flux by Road**.
3. Kies je inlogmethode:
   - **Inloggen met E-Flux**: Vul je e-mailadres en wachtwoord in. Als E-Flux om browserverificatie of tweestapsverificatie vraagt, gebruik je de sessietokenoptie.
   - **Bestaande E-Flux-sessie**: Kopieer de complete sessie-export uit je browser en plak die in Home Assistant. Zie [Sessie-export kopiëren](#sessie-export-kopiëren).
4. Selecteer een gevonden laadpas en laadpunt. Als je laadpunt niet gevonden wordt, kun je de gegevens handmatig invoeren. Via **Configureren** kun je deze keuze later aanpassen.

De sessie-energiesensor toont de energie van de lopende sessie en is geen doorlopende meter voor het Energiedashboard. Niet iedere provider geeft actuele sessiewaarden door. De integratie gebruikt niet-gedocumenteerde onderdelen van het gebruikersdashboard; wijzigingen bij Road kunnen de werking beïnvloeden. Een start of stop met onzekere uitkomst blijft geblokkeerd totdat de uitkomst bevestigd kan worden.

### Sessie-export kopiëren

Deze stappen zijn bedoeld voor **Google Chrome op een computer**. Het commando werkt in de console van Chrome, niet in de adresbalk of in Home Assistant.

1. Open [het E-Flux-dashboard](https://dashboard.e-flux.io/) in Chrome en log in met het account waarmee je de laadpas gebruikt. Laat dit tabblad open.
2. Open **Console** in datzelfde E-Flux-tabblad:
   - **macOS:** druk op `⌘ Command` + `⌥ Option` + `J`.
   - **Windows of Linux:** druk op `Ctrl` + `Shift` + `J`.
   - Je kunt ook via het Chrome-menu **Meer hulpprogramma's** → **Hulpprogramma's voor ontwikkelaars** openen en daar **Console** kiezen.
3. Controleer of zowel de sessie als het apparaat-token aanwezig zijn. Typ of plak de volgende regel achter de `>` in de console en druk op Enter:

   ```javascript
   Boolean(
     (sessionStorage.getItem('e_flux_jwt') || localStorage.getItem('e_flux_jwt')) &&
     localStorage.getItem('e_flux_device_token')
   )
   ```

   Verschijnt `true`, ga dan verder. Bij `false` ontbreekt minstens één waarde: controleer of je bent ingelogd en of de console bij het E-Flux-tabblad hoort. Als opnieuw inloggen niet helpt, kan E-Flux de opslagmethode hebben gewijzigd.

4. Kopieer beide waarden als één beveiligde export naar je klembord met deze regel en druk op Enter:

   ```javascript
   copy([
     sessionStorage.getItem('e_flux_jwt') || localStorage.getItem('e_flux_jwt'),
     localStorage.getItem('e_flux_device_token')
   ].join('|'))
   ```

   `undefined` als resultaat is hierbij normaal: de export staat op je klembord en wordt niet in de console weergegeven.
5. Ga terug naar Home Assistant en kies in de E-Flux-installatieflow **Bestaande E-Flux-sessie**. Plak de volledige export in het afgeschermde veld met `⌘ Command` + `V` op Mac of `Ctrl` + `V` op Windows/Linux en ga verder. Kopieer en plak op hetzelfde apparaat; een klembord wordt niet automatisch tussen apparaten gedeeld.

**Bestaande installatie bijwerken:** ga naar **Instellingen** → **Apparaten en diensten** → **E-Flux by Road** → **Opnieuw configureren** en plak daar dezelfde volledige export. De laadpas- en laadpuntkeuze blijven behouden.

**Liever zonder commando?** Open in Chrome DevTools het tabblad **Application**. Kijk onder **Session storage** en, als het daar ontbreekt, onder **Local storage** bij de oorsprong `https://dashboard.e-flux.io`. Noteer daar de sleutels `e_flux_jwt` en `e_flux_device_token`; beide zijn nodig. De consolemethode is veiliger, omdat je dan niet handmatig een JSON-export hoeft te maken.

Behandel de volledige export als een wachtwoord: deel hem niet via chat, screenshots of GitHub. Zolang Road de gekoppelde sessie accepteert, ververst Home Assistant deze zelf. Log je uit, trekt Road het apparaat-token in, of faalt een vernieuwing, dan vraagt Home Assistant om opnieuw te configureren. Tot die tijd blokkeert de integratie een nieuwe start of stop liever dan met onzekere inloggegevens te handelen.

---

## Automatisering: Automatisch starten bij inpluggen Tesla

Een automatisering moet de daadwerkelijke aansluiting en de juiste auto betrouwbaar herkennen. Tesla-sensoren en de kaartstatus van E-Flux kunnen vertraagd zijn; controleer de beschikbare lokale laadpaalstatus en de bestaande voertuigdetectie voordat je automatisch starten activeert. De kaartstatus `CHARGING` bewijst niet dat er energie wordt geleverd.
