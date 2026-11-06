# E-Flux by Road voor Home Assistant

Start en volg je E-Flux (Road.io) laadsessies rechtstreeks vanuit Home Assistant. Ideaal voor het automatisch starten van laadsessies wanneer je je auto (zoals een Tesla) inplugt.

## Wat het doet

- **Sessie starten op afstand**: Start laadsessies op je gekoppelde E-Flux laadpaal (zoals een Alfen Eve) met je geconfigureerde laadpas.
- **Automatische retry bij afwijzing**: Handelt betrouwbaar de situatie af waarbij E-Flux/laadpaalbeheerder de eerste startpoging(en) weigert (`REJECTED_BY_CPO` of HTTP 500) totdat de connector gereed is. Wacht tussen pogingen en stopt zodra de sessie actief is.
- **Sensoren & Statussen**:
  - `sensor.e_flux_laadpaal_connectorstatus`: Actuele connectorstatus (bijv. `AVAILABLE`, `PREPARING`, `CHARGING`, `OCCUPIED`).
  - `sensor.e_flux_laadpaal_actieve_sessies`: Aantal actieve laadsessies op je account.
  - `sensor.e_flux_laadpaal_laatste_startstatus`: Status van het laatste startverzoek (`STARTING`, `COMPLETED`, `CANCELLED` met foutreden in attributen).
  - `sensor.e_flux_laadpaal_laatste_startverzoek`: Statusmelding van de startflow.
- **Bediening**:
  - Knop: `button.e_flux_laadpaal_start_laadsessie`
  - Actie/Service: `eflux_road.start_session`
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
   - **Bestaande E-Flux-sessie**: Kopieer het JWT-token (`e_flux_jwt`) uit de Developer Tools van je browser op `dashboard.e-flux.io`.
4. Bevestig de gegevens van je laadpaal en laadpas (deze staan standaard al ingevuld voor je Alfen laadpaal en pas).

---

## Automatisering: Automatisch starten bij inpluggen Tesla

Wanneer je de laadkabel in je Tesla steekt, detecteert Home Assistant dit via `binary_sensor.charger`. Met onderstaande automatisering start de laadsessie automatisch:

```yaml
alias: "Tesla - Automatisch laden starten via E-Flux"
description: "Start laadsessie zodra de kabel in de Tesla wordt gestoken en de auto thuis staat"
mode: single
trigger:
  - platform: state
    entity_id: binary_sensor.charger
    to: "on"
condition:
  - condition: state
    entity_id: binary_sensor.charging
    state: "off"
  - condition: numeric_state
    entity_id: sensor.e_flux_laadpaal_actieve_sessies
    below: 1
action:
  - service: button.press
    target:
      entity_id: button.e_flux_laadpaal_start_laadsessie
```
