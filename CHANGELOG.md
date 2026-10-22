# Changelog

## 0.2.1

- Correctif : les arguments des appels d'outils sont masqués après décodage JSON. Avant, un faux email comme un décorateur Python après un retour à la ligne pouvait laisser un antislash orphelin et le fournisseur rejetait la requête (erreur 400, invalid tool call arguments).

## 0.2.0

- Commande `/masquer` : status, on, off, ajouter, retirer, liste
- Masquage auto : emails, téléphones FR, IBAN, adresses FR claires, clés
- Liste de noms à ajouter soi-même
- Masquage à la demande d'un texte, d'un Markdown ou d'un PDF
- Restauration des vraies valeurs pour les outils
