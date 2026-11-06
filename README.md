# E-Flux by Road voor Home Assistant

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
  - Direct inloggen met E-Flux e-mailadres en wachtwoord.
  - Of sessietoken (`e_flux_jwt` uit de browser) als E-Flux extra verificatie of Cloudflare bot-checks vereist.
  - Automatische sessieverlenging op de achtergrond.

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
   - **Inloggen met E-Flux**: Vul je e-mailadres en wachtwoord in.
   - **Bestaande E-Flux-sessie**: Kopieer je sessietoken uit je browser en plak het in Home Assistant. Zie [Sessietoken kopiëren](#sessietoken-kopiëren).
4. Selecteer een gevonden laadpas en laadpunt. Als je laadpunt niet gevonden wordt, kun je de gegevens handmatig invoeren. Via **Configureren** kun je deze keuze later aanpassen.

De sessie-energiesensor toont de energie van de lopende sessie en is geen doorlopende meter voor het Energiedashboard. Niet iedere provider geeft actuele sessiewaarden door. De integratie gebruikt niet-gedocumenteerde onderdelen van het gebruikersdashboard; wijzigingen bij Road kunnen de werking beïnvloeden. Een start of stop met onzekere uitkomst blijft geblokkeerd totdat de uitkomst bevestigd kan worden.

### Sessietoken kopiëren

Deze stappen zijn bedoeld voor **Google Chrome op een computer**. Het commando werkt in de console van Chrome, niet in de adresbalk of in Home Assistant.

1. Open [het E-Flux-dashboard](https://dashboard.e-flux.io/) in Chrome en log in met het account waarmee je de laadpas gebruikt. Laat dit tabblad open.
2. Open **Console** in datzelfde E-Flux-tabblad:
   - **macOS:** druk op `⌘ Command` + `⌥ Option` + `J`.
   - **Windows of Linux:** druk op `Ctrl` + `Shift` + `J`.
   - Je kunt ook via het Chrome-menu **Meer hulpprogramma's** → **Hulpprogramma's voor ontwikkelaars** openen en daar **Console** kiezen.
3. Controleer of de sessie een token bevat. Typ of plak de volgende regel achter de `>` in de console en druk op Enter:

   ```javascript
   Boolean(sessionStorage.getItem('e_flux_jwt') || localStorage.getItem('e_flux_jwt'))
   ```

   Verschijnt `true`, ga dan verder. Bij `false` is het token op deze plek niet gevonden: controleer of je bent ingelogd en of de console bij het E-Flux-tabblad hoort. Als opnieuw inloggen niet helpt, kan E-Flux de opslagmethode hebben gewijzigd.

4. Kopieer het token naar je klembord met deze regel en druk op Enter:

   ```javascript
   copy(sessionStorage.getItem('e_flux_jwt') || localStorage.getItem('e_flux_jwt'))
   ```

   `undefined` als resultaat is hierbij normaal: de waarde staat op je klembord en wordt niet in de console weergegeven.
5. Ga terug naar Home Assistant en kies in de E-Flux-installatieflow **Bestaande E-Flux-sessie**. Plak het token in het veld **E-Flux sessietoken (JWT)** met `⌘ Command` + `V` op Mac of `Ctrl` + `V` op Windows/Linux en ga verder. Kopieer het token en plak het op hetzelfde apparaat; een klembord wordt niet automatisch tussen apparaten gedeeld.

**Liever zonder commando?** Open in Chrome DevTools het tabblad **Application**. Kijk onder **Session storage** en, als het daar ontbreekt, onder **Local storage** bij de oorsprong `https://dashboard.e-flux.io`. Zoek de sleutel `e_flux_jwt`, kopieer de waarde en plak die in Home Assistant. Als Chrome plakken in de console blokkeert, kun je deze methode gebruiken.

Behandel het sessietoken als een wachtwoord: deel het niet via chat, screenshots of GitHub. Vraagt Home Assistant later om opnieuw te verbinden, log dan opnieuw in bij E-Flux en herhaal deze stappen voor een nieuw token.

---

## Automatisering: Automatisch starten bij inpluggen Tesla

Een automatisering moet de daadwerkelijke aansluiting en de juiste auto betrouwbaar herkennen. Tesla-sensoren en de kaartstatus van E-Flux kunnen vertraagd zijn; controleer de beschikbare lokale laadpaalstatus en de bestaande voertuigdetectie voordat je automatisch starten activeert. De kaartstatus `CHARGING` bewijst niet dat er energie wordt geleverd.
