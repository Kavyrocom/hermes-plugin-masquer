# Masquer

Plugin [Hermes Agent](https://hermes-agent.nousresearch.com). Il cache emails, téléphones, IBAN, cartes, adresses claires, clés et noms de personnes **avant** l'envoi au modèle.

Toi, tu continues à tout lire normalement. Les outils retrouvent les vraies valeurs pour travailler.

Ce n'est pas une conformité RGPD. C'est un filet.

## Ce qu'il cache tout seul

- emails
- téléphones français (`0X`, `+33`, `+33 (0)X`, `(+33)`) et internationaux (11 à 15 chiffres après `+` ou `00`)
- IBAN, français ou étrangers, validés par la clé mod 97 (BE, DE, CH, GB, LU...)
- cartes bancaires, validées par la clé de Luhn
- adresses du type `12 rue de la Paix`, `3 bis avenue Foch`
- clés API : OpenAI, Anthropic, OpenRouter, GitHub, Slack, AWS, Stripe (`sk_live` et `whsec`), Notion, Airtable, Telegram, Discord, Google, JWT, blocs de clé privée PEM
- noms de personnes : `Marie Dupont` devient `[PRENOM_1] [NOM_1]`

## Les noms de personnes

Rien à fournir, rien à installer : aucun modèle, aucune liste de contacts. Le plugin embarque un dictionnaire de prénoms et de mots courants, et s'appuie sur quatre indices :

- un prénom connu suivi d'un nom (`Marie Dupont`, `DUPONT Marie`, `Marie DUPONT`)
- un champ qui porte un nom (`first_name`, `last_name`, `author`, `From:`)
- une formule (`Bonjour Thomas,`, `M. Vasseur`, une signature)
- la mémoire de la session : une personne vue une fois reste masquée ensuite, même par son prénom seul

La même personne garde le même jeton pendant toute la session. L'agent suit donc qui est qui sans lire le nom.

**Personnes publiques : la règle suit la provenance du texte, pas la personne.**

- Texte privé (tes messages, fichiers, emails, sorties d'outils métier) : les noms sont masqués.
- Texte du web (résultats de recherche, pages lues) : les noms restent lisibles. Un ministre ou `Louis Vuitton` dans un article ne sont pas cachés.
- Le privé l'emporte : un nom déjà vu dans un texte privé reste masqué partout, y compris dans une page web.

Les outils considérés comme « web » sont `web_search`, `web_extract`, `web_fetch` et `x_search`. Pour changer la liste, écris un motif par ligne dans `pii-mask/outils-web.txt`.

Ce que la détection laisse passer :

- un prénom seul, sans formule ni personne déjà vue (`Camille` dans une phrase)
- un nom écrit en minuscules
- un prénom absent du dictionnaire suivi d'un nom lui aussi inconnu
- les langues autres que le français et l'anglais, moins bien couvertes

Ce qu'elle masque parfois à tort : une société qui porte un nom de personne (`Groupe Martin`), une marque à prénom. Un faux positif ne casse rien, les outils retrouvent la vraie valeur. Pour l'éviter : `/masquer exclure Martin`.

Mesure sur 25 Mo d'historique réel d'un agent (français et anglais, 5 220 messages privés) : environ 8 détections sur 10 sont des personnes, 1 sur 10 une société ou un lieu qui porte un nom de personne, 1 sur 10 une erreur. Environ 4 noms complets sur 5 sont attrapés. Coût : 2 Mo de texte par seconde. Ces chiffres viennent d'un seul corpus, étiqueté à la main sur échantillon : prends-les comme un ordre de grandeur.

Pour forcer un nom que la détection rate :

Un code postal suivi d'un mot n'est plus masqué seul : ce format cachait trop de faux positifs (sorties d'outils, chiffres métier, dates). Une adresse se masque dans un format rue complet avec un nom.

```
/masquer ajouter Marie Dupont
/masquer liste
/masquer retirer Marie Dupont
```

## Installer

Colle ce prompt à ton agent Hermes. Lis l'audit, puis donne le GO.

```text
Installe le plugin Hermes Masquer depuis GitHub sur mon profil actuellement utilisé.

Source : https://github.com/Kavyrocom/hermes-plugin-masquer

Objectif : avant chaque envoi au modèle dans le cloud, masquer emails, téléphones, IBAN, cartes, adresses françaises claires et clés. Je dois continuer à tout lire normalement. Les outils doivent retrouver les vraies valeurs. Ce n'est pas une conformité RGPD.

Avant toute modification
1. Identifie le profil actif, la version Hermes, et si le plugin Masquer est déjà là.
2. Lis l'aide actuelle des plugins Hermes et la documentation officielle du moment. Ne te fie pas à une commande mémorisée.
3. Vérifie le dépôt GitHub, sa licence et que l'installation se fait bien comme un plugin Hermes, pas comme un skill.
4. Explique-moi en français simple : bénéfice, risques, ce qui sera modifié, ce qui ne le sera pas.
5. Attends mon GO explicite. Sans GO, tu t'arrêtes.

Après GO
1. Installe et active uniquement sur le profil actif. N'écrase pas un autre profil.
2. N'envoie aucun secret, aucun vrai client, aucun vrai email dans tes preuves.
3. Ne redémarre pas la passerelle Discord ou un autre canal sans un GO séparé.
4. Fais un test avec uniquement des fausses données : marie.dupont@exemple.fr, 06 12 34 56 78, 12 rue de la Paix.
5. Vérifie que le mode auto est allumé et que /masquer status répond.
```

L'agent lancera l'équivalent de :

```bash
hermes plugins install Kavyrocom/hermes-plugin-masquer --enable
```

Puis relance une session (ou la passerelle, si tu l'utilises) pour que la commande `/masquer` apparaisse.

## Commandes

| Commande | Effet |
|---|---|
| `/masquer status` | État du mode auto, version, auto-test |
| `/masquer test` | Contrôle du masquage sur fausses données |
| `/masquer on` | Allume le filtre |
| `/masquer off` | Coupe le filtre. Le masquage à la demande marche encore |
| `/masquer ajouter Marie Dupont` | Ajoute un nom à masquer |
| `/masquer liste` | Montre les noms ajoutés |
| `/masquer noms on` / `noms off` | Allume ou coupe la détection automatique des noms. Le réglage est gardé |
| `/masquer exclure Mistral` | Ce mot n'est plus jamais pris pour une personne |
| `/masquer ...` | Masque un texte collé, ou un fichier (Markdown, PDF) |

## Vérifier

```
/masquer status
/masquer test
```

L'auto-test doit afficher 10/10 OK. `On se voit à Paris demain` ne doit pas bouger.

## Limites

- Le masquage couvre les requêtes de la boucle principale. Les appels auxiliaires d'Hermes (compression de contexte, titres de session, requêtes mémoire, vision) ne passent pas par les middlewares de plugin : leur contenu part tel quel au fournisseur.
- Les dates de naissance, numéros de sécurité sociale, mots de passe d'application et adresses inversées (`Rue de la Paix 12`) ne sont pas masqués.
- Les clés intégrées dans des URL (`?api_key=`), les variables d'environnement `.env` et les jetons `Bearer` génériques ne sont pas masqués : les distinguer d'un texte ordinaire est trop incertain.
- La détection des noms est un filet, pas une garantie : voir « Les noms de personnes ».
- Les adresses vagues ne sont pas devinées.
- Le mapping reste local à ta machine, et la table d'une session est purgée à sa fin. Ne publie jamais un `/masquer status` qui collerait des vraies valeurs.
- Un PDF se lit si l'outil de lecture PDF est disponible sur la machine. Sinon, passe par le texte ou le Markdown.

## Données embarquées

Le dossier `data/` contient les listes utilisées pour les noms. Elles se régénèrent avec `scripts/build_data.py`.

| Fichier | Source | Licence |
|---|---|---|
| `prenoms.txt.gz`, `prenoms-rares.txt.gz` | INSEE, Fichier des prénoms (état civil, édition 2023) ; `sigpwned/popular-names-by-country-dataset` | Licence Ouverte 2.0 ; CC0-1.0 |
| `noms.txt.gz` | `sigpwned/popular-names-by-country-dataset` | CC0-1.0 |
| `mots.txt.gz` | `hermitdave/FrequencyWords` (2018, français et anglais) | MIT |
| `prenoms-ambigus.txt` | liste du projet | MIT |

Source : Insee, Fichier des prénoms. Les données ont été filtrées et normalisées.

## Licence

MIT. Fait pour [Kavyro](https://kavyro.com).