# Changelog

## 0.3.0

- Correctif : le motif « code postal + ville » est retiré. Il masquait des faux positifs (sorties d'outils, chiffres métier, dates). Une adresse ne se masque plus que dans un format rue complet avec un nom en majuscule.
- Correctif : un jeton déjà connu de la session n'est plus re-numéroté. Le modèle qui réutilise un jeton dans un appel d'outil ou sa réflexion relit le même, et les outils reçoivent la vraie valeur.
- Correctif : les blocs de réflexion signés ne sont plus touchés. La signature reste valide, le fournisseur ne rejette plus le tour rejoué.
- Correctif : les numéros +33 (0)X et (+33) X sont masqués, et les dates précédées d'un indicatif ne sont plus prises pour des téléphones.
- Ajout : numéros internationaux (11 à 15 chiffres après + ou 00).
- Ajout : IBAN de tous pays, validés par la clé mod 97.
- Ajout : cartes bancaires, validées par la clé de Luhn.
- Ajout : clés Stripe whsec, Notion, Airtable, Telegram, Discord, Google ya29, blocs de clé privée PEM.
- Ajout : /masquer status affiche la version et un auto-test ; /masquer test le détaille.
- Ajout : la table d'une session est purgée à la fin de la session.

## 0.2.4

- Correctif : un identifiant de processus de 5 chiffres suivi d'un jour ou d'un mois abrégé (`Sat`, `Oct`...) n'est plus pris pour un code postal et une ville.

## 0.2.3

- Correctif : un nom d'unité systemd (`getty@tty1.service`, `.timer`, `.socket`...) n'est plus pris pour un email et masqué.

## 0.2.2

- Correctif : un texte de plus de 2 millions de caractères n'est plus renvoyé en clair. Il est découpé en morceaux de 500 000 caractères et masqué.
- Correctif : le prompt système, les définitions d'outils et les clés `prompt`, `contents`, `system_instruction` sont masqués comme les messages.
- Correctif : un nom ajouté à la liste ne masque plus que des mots entiers (« Ana » ne touche plus « banane »).
- Correctif : la regex email n'est plus quadratique (4,5 s sur 80 000 caractères, 0,007 s maintenant).
- Un texte qui contient déjà un jeton du type `marie.dupont@exemple.fr` n'est plus restauré à tort comme une valeur privée.
- Plus de clés API reconnues : GitHub (gho, ghu, ghs, ghr), Stripe, Slack, Google, JWT.
- `/masquer status` compte les valeurs de toutes les sessions.

## 0.2.1

- Correctif : les arguments des appels d'outils sont masqués après décodage JSON. Avant, un faux email comme un décorateur Python après un retour à la ligne pouvait laisser un antislash orphelin et le fournisseur rejetait la requête (erreur 400, invalid tool call arguments).

## 0.2.0

- Commande `/masquer` : status, on, off, ajouter, retirer, liste
- Masquage auto : emails, téléphones FR, IBAN, adresses FR claires, clés
- Liste de noms à ajouter soi-même
- Masquage à la demande d'un texte, d'un Markdown ou d'un PDF
- Restauration des vraies valeurs pour les outils
