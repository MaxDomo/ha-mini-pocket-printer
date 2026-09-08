# Journal des versions

## 1.1.0-beta

**Transport Bluetooth classique retiré.** L'appairage manuel, le canal RFCOMM
qui restait occupé et l'absence de relais par les proxys le rendaient trop
instable. Reste l'USB et le BLE, l'un et l'autre éprouvés.

**Les actions rendent la main aussitôt.** L'impression part en tâche de fond ;
`wait: true` rétablit l'attente quand on veut remonter l'erreur.

**Ajouté**

- diagnostic téléchargeable depuis la fiche de l'appareil ;
- logo Home Assistant en en-tête du ticket de test ;
- péremption des travaux en attente, en heures ;
- traductions française et anglaise des noms d'entités.

**Corrigé**

- écritures USB partielles, qui interrompaient une impression en cours ;
- découverte d'un nouvel exemplaire empêchée par la réécriture d'adresse ;
- entités qui restaient affichées alors que l'imprimante ne répondait plus ;
- entités non prévenues d'un relevé réussi, d'où des valeurs figées ;
- déclaration des cibles de service refusée par Hassfest ;
- rechargement d'entrée signalé comme obsolète par Home Assistant.

## 1.0.0-beta

Première version : impression de texte, tableaux, images, QR codes,
codes-barres et ticket météo illustré. Transports USB, Bluetooth classique et
BLE. Réglages de densité et de mise en veille lus et écrits dans le firmware.
