fuel_station - Gestion de stations-service pour Tryton 7.0
==========================================================

Module Tryton 7.0 pour gérer des stations-service : stations, cuves, pompes
et pistolets, prix des carburants, quarts de travail avec index des
compteurs, livraisons de carburant, jaugeages avec ajustement de stock,
ventes par jour/carburant, rapport de quart.

Nouveautés 7.0.2 : gallon comme unité par défaut, prénom des personnes
physiques, contrôle des billets et pièces (faux billets compris), relevés
de compteur (API et import CSV), correction de température/densité,
ventes boutique/GPL. Le module compagnon *fuel_station_invoice* facture
les ventes à crédit.

Les quantités passent par le module natif *stock* : chaque cuve est un
emplacement de stock, chaque vente et chaque livraison est un mouvement.

Installation::

    pip install fuel_station-tryton
    trytond-admin -d MABASE -u fuel_station --activate-dependencies -l fr

Groupes : « Station-service » (exploitation), « Administration
station-service » (configuration). Guides PDF : dossier ``docs`` de
l'archive.

Nouveautés 7.0.3 : prénom visible dans la liste des tiers, autorisations
de distribution (demande, validation, middleware, journal d'audit,
expiration, blocage), guide d'utilisation dans le menu. Le module
*fuel_station_sale* crée ventes et factures. Tryton ne pilote jamais les
pompes : le contrôleur certifié décide, le middleware doit être validé par
le fabricant et l'installateur habilité.
