# Mini Pocket Printer

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.1.0--beta-orange.svg)](https://github.com/MaxDomo/ha-mini-pocket-printer/releases)
[![Licence](https://img.shields.io/badge/licence-MIT-green.svg)](LICENSE)

Imprimez depuis Home Assistant sur les petites imprimantes thermiques
**Tronic 5890** (Lidl) et leurs clones, vendus sous le nom générique
« Mini Pocket Printer ».

Texte, tableaux, images, QR codes, codes-barres, et un ticket météo illustré.
En USB — le plus fiable — ou en BLE via un proxy Bluetooth.

[![Ouvrir dans HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MaxDomo&repository=ha-mini-pocket-printer&category=integration)

> **Version bêta.** Le protocole a été reconstitué par rétro-ingénierie et
> vérifié sur un seul modèle. Signalez ce qui casse.

---

## Installation

**Via HACS** — le bouton ci-dessus ajoute le dépôt à votre installation.
Installez, puis redémarrez Home Assistant.

**Manuellement** — copiez `custom_components/mini_pocket_printer` dans votre
dossier `config`, puis redémarrez.

Ensuite, l'imprimante allumée est détectée automatiquement. Sinon :

[![Ajouter l'intégration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=mini_pocket_printer)

---

## Premiers pas

Une fois l'imprimante ajoutée, essayez le bouton **Ticket de test** sur sa
fiche : il imprime un état des lieux et confirme que tout fonctionne.

Puis, depuis Outils de développement > Actions :

```yaml
action: mini_pocket_printer.print_text
data:
  text: Bonjour
```

---

## Ce que vous pouvez imprimer

| Action | Contenu |
| ------ | ------- |
| `print_text` | texte multi-ligne, avec retour à la ligne automatique |
| `print_table` | tableau à colonnes, largeurs calculées sur le contenu |
| `print_image` | image locale ou distante, tramée pour le thermique |
| `print_qr` | QR code, avec légende |
| `print_barcode` | code-barres : code128, ean13, upca et une dizaine d'autres |
| `print_weather` | ticket météo illustré depuis une entité `weather` |
| `feed` | avance papier |
| `cancel` | vide la file d'attente |

Toutes acceptent `device_id`, `size`, `font` et `feed`.

**Les actions rendent la main aussitôt.** Une impression prend plusieurs
secondes, et une automatisation n'a pas à rester suspendue pendant ce temps :
le travail part en tâche de fond, les échecs sont journalisés. Ajoutez
`wait: true` quand vous voulez que l'action attende et remonte l'erreur.

### Exemples

```yaml
action: mini_pocket_printer.print_table
data:
  title: COURSES
  headers: ["Article", "Qte", "Prix"]
  align: ["left", "center", "right"]
  rows:
    - ["Pain complet", 2, "3,20"]
    - ["Cafe", 3, "7,50"]
```

```yaml
action: mini_pocket_printer.print_qr
data:
  data: "WIFI:S:MonReseau;T:WPA;P:motdepasse;;"
  label: Wi-Fi invites
  error_correction: H
```

```yaml
action: mini_pocket_printer.print_weather
data:
  weather_entity: weather.home
  days: 3
```

Le ticket météo compose lui-même sa mise en page : grand pictogramme pour les
conditions du moment, température en gros caractères, puis une ligne par jour
avec son symbole. Les pictogrammes sont tracés géométriquement, sans fichier
d'image, donc nets à toute taille.

### Le ticket du matin

```yaml
triggers:
  - trigger: time
    at: "07:00:00"
actions:
  - action: mini_pocket_printer.print_weather
    data:
      weather_entity: weather.home
      days: 3
      title: BONJOUR
  - action: mini_pocket_printer.print_text
    data:
      feed: 150
      text: "Premier RDV : {{ state_attr('calendar.perso','message') }}"
```

---

## Polices

Deux polices sont embarquées, sous licence SIL Open Font License :

- `receipt` — Courier Prime, machine à écrire, par défaut
- `receipt-bold` — la même en graisse renforcée
- `dotmatrix` — VT323, matriciel façon caisse enregistreuse

```yaml
data:
  font: dotmatrix
  size: 36
```

`font` accepte aussi le chemin d'un `.ttf`. Et `/config/fonts/receipt.ttf`, s'il
existe, remplace la police par défaut partout sans rien changer aux actions.

Le corps par défaut est 36. Les colonnes des tableaux et du ticket météo
s'ajustent automatiquement à la police et à la taille choisies.

---

## Transports

Deux chemins, avec le **même protocole**. Le choix se fait dans les options de
l'entrée.

| Valeur | Quand l'utiliser |
| ------ | ---------------- |
| `usb` | imprimante branchée au serveur — le plus fiable |
| `ble` | imprimante à distance, via un proxy Bluetooth |
| `auto` | **par défaut** : USB si un périphérique est configuré, BLE sinon |

L'imprimante expose aussi une face Bluetooth classique en SPP. Elle n'est pas
prise en charge : l'appairage manuel, le canal qui reste occupé et l'absence
de relais par les proxys la rendaient trop instable pour être proposée.

### USB

Le transport à privilégier quand c'est possible : ni portée, ni appairage, ni
canal à se disputer. Choisissez le périphérique dans les options — en général
`/dev/usb/lp0`.

Attention au câble : beaucoup de câbles USB-C ne transportent que
l'alimentation.

### BLE

Fonctionne à travers la maison grâce aux **proxys Bluetooth ESPHome**, à
condition qu'un proxy soit à quelques mètres de l'imprimante. Les annonces
portent bien plus loin que les connexions : voir l'imprimante ne suffit pas à
pouvoir lui parler.

L'entité **Signal** indique où elle est entendue, et avec quelle puissance.
Au-dessus de -80 dBm la connexion tient, en dessous de -90 elle devient
aléatoire.

---

## Entités

| Entité | Rôle |
| ------ | ---- |
| Densité | faible, moyenne, forte |
| Mise en veille | minutes avant extinction automatique |
| Interrogation périodique | intervalle de rafraîchissement, 10 min par défaut |
| Limite de file | travaux maximum en attente |
| Péremption des travaux | heures avant abandon d'un travail en attente |
| Batterie | pourcentage |
| Papier | signale un rouleau absent |
| Signal | puissance reçue, avec le détail par point d'écoute |
| File d'attente | travaux en cours et en attente |
| Dernière impression | horodatage, et diagnostic en attributs |
| Ticket de test | imprime un état des lieux |
| Annuler la file | vide les travaux en attente |
| Relire les réglages | force une lecture |

Densité et mise en veille écrivent dans le firmware : le réglage survit à un
redémarrage et s'applique aussi quand vous imprimez depuis le téléphone.

**Aucune valeur n'est mise en cache.** Une lecture qui échoue vide le champ
plutôt que d'afficher un relevé ancien.

**Tout passe en indisponible** quand l'imprimante n'est plus détectée. Les
entités réagissent immédiatement, sans attendre un cycle : une valeur affichée
alors que l'imprimante est absente induit en erreur.

Les réglages de l'intégration — limite de file, péremption, interrogation
périodique — restent modifiables, eux ne dépendent pas de l'imprimante.

---

## Bon à savoir

**Une connexion à la fois.** L'imprimante n'accepte qu'un seul lien : si votre
téléphone est connecté, Home Assistant échouera. Chaque action se connecte,
imprime, puis se déconnecte.

**La mise en veille.** Une imprimante endormie cesse d'annoncer et devient
injoignable jusqu'à un appui sur son bouton. Réglez **Mise en veille** au
maximum, ou rapprochez l'**Interrogation périodique**, qui repousse le
compteur d'inactivité à chaque passage.

**Plusieurs imprimantes.** Elles annoncent toutes le même nom : c'est l'adresse
qui les distingue, et le titre de l'entrée en reprend les quatre derniers
chiffres. Ciblez l'appareil voulu dans vos actions.

```yaml
action: mini_pocket_printer.print_text
data:
  device_id: <imprimante>
  text: Bonjour
```

**Travaux périmés.** Un ticket du matin qui sortirait en fin de journée
n'intéresse personne. **Péremption des travaux** fixe une durée en heures
au-delà de laquelle un travail en attente est abandonné plutôt qu'imprimé.
`0` les conserve indéfiniment.

**Diagnostic.** La fiche de l'appareil propose « Télécharger le diagnostic » :
un JSON avec le transport actif, la dernière erreur, l'état d'appairage, les
points d'écoute et toutes les valeurs lues. À joindre à un rapport de bogue.

**Une entrée par imprimante.** Chacune s'annonce sous deux adresses, `5E:55:…`
et `55:55:…`. L'intégration les reconnaît comme un seul appareil, quelle que
soit celle par laquelle elle a été découverte.

---

## Auteur

Maxime Maucourant — mmaucourant@gmail.com — [@MaxDomo](https://github.com/MaxDomo)

Protocole reconstitué par rétro-ingénierie à partir de captures btsnoop HCI et
de sondages du firmware. Les polices embarquées sont sous licence SIL Open Font
License, voir `custom_components/mini_pocket_printer/fonts/`.
