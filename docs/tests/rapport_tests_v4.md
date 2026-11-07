# Rapport de tests - final_suite v4

- **Date :** 6 novembre 2026
- **Testeur :** Bastien (GuyBastien)
- **Environnement :** serveur de démonstration de la communauté Tryton Haïti, bases `hotel`, `resto` et `gaz`, comptes MonCash et NatCash en mode test (sandbox)

## Résumé

| # | Test | Résultat |
|---|---|---|
| 1 | Disponibilité du serveur | ✅ Après la mise à jour (502 pendant la mise à jour) |
| 2 | Connexion de démonstration sans mot de passe | ✅ |
| 3 | Paiement MonCash par lien et code QR | ✅ |
| 4 | Paiement MonCash par USSD | ⚠️ Instructions correctes, paiement impossible à finaliser en sandbox |
| 5 | Notifications push | ❌ Aucune cible configurée |
| 6 | NatCash | ⚠️ Paramètres validés, aucun paiement testé |
| 7 | Paiement mobile sur la base `gaz` | ❌ Non disponible |

## Détail

### 1. Disponibilité du serveur

Le serveur a d'abord répondu **HTTP 502 Bad Gateway** sur les trois bases : nginx répondait, mais trytond était arrêté pour la mise à jour (ajout des notifications push et de l'USSD). Une fois la mise à jour terminée, le serveur est revenu (HTTP 200).

### 2. Connexion

Base `hotel`, utilisateur `admin`, mot de passe vide : la connexion fonctionne.

### 3. Paiement MonCash par lien et code QR

Depuis une facture client postée (100 HTG), bouton **Payer par mobile**, compte MonCash, canal **Lien / code QR**.

- L'API sandbox MonCash renvoie un lien de paiement valide.
- Le lien et le code QR s'ouvrent sur la page MonCash.
- Le code OTP de test est bien reçu.
- **Résultat : chaîne de paiement validée.**

### 4. Paiement MonCash par USSD

Même facture, canal **USSD**.

- Le code `*202#` et les instructions pour le client sont générés correctement, avec les liens de partage SMS et WhatsApp.
- Le bouton **Vérifier le paiement** fonctionne : la transaction reste « en attente », ce qui est normal en sandbox.
- Le bouton **Confirmer le reçu** refuse un faux numéro de reçu avec un message clair, car le compte n'autorise pas la confirmation manuelle. **Le contrôle de sécurité fonctionne.**
- Test sur un vrai téléphone : le paiement ne peut pas aboutir, car `202` est un code marchand de démonstration et le compte est en sandbox.

### 5. Notifications push

Le modèle `mobile.payment.push` existe, mais aucune cible n'est configurée sur la base `hotel`. Aucune notification ne peut donc être envoyée.

### 6. NatCash

Les paramètres du compte NatCash sont validés en mode test. Aucun paiement n'a été effectué.

### 7. Base `gaz`

Aucun compte de paiement mobile n'existe sur la base `gaz`. Le dépôt ne contient pas de module `fuel_station_mobile_payment`, alors que l'hôtel, le restaurant et le point de vente en ont un.

## Recommandations

1. **Page de maintenance.** Afficher une page « mise à jour en cours » à la place de l'erreur 502 (`error_page 502` dans nginx), et vérifier automatiquement après chaque redémarrage que trytond répond.
2. **Accès de démonstration.** Le serveur est public et l'accès `admin` se fait sans mot de passe : utiliser uniquement des données fictives, ou limiter l'accès (adresses IP ou authentification HTTP devant nginx).
3. **Notifications push.** Configurer au moins une cible, par exemple de type `ntfy` avec un sujet secret, pour tester la fonction.
4. **USSD en démonstration.** Pour montrer un paiement USSD jusqu'à « payé », activer **Autoriser la confirmation manuelle** sur le compte de test uniquement. En production, laisser cette option désactivée.
5. **NatCash.** Effectuer un paiement de test complet.
6. **Station-service.** Préciser si le paiement mobile est prévu pour les stations-service.
7. **Licences.** Les 7 modules de paiement mobile (`mobile_payment`, `moncash`, `natcash`, `hotel_mobile_payment`, `restaurant_mobile_payment`, `sale_mobile_payment`, `hotel_restaurant`) déclarent la licence AGPL-3, les 23 autres GPL-3. Confirmer que c'est voulu et ajouter les fichiers de licence correspondants.
