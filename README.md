# Masquer

Plugin [Hermes Agent](https://hermes-agent.nousresearch.com). Il cache emails, téléphones français, IBAN, adresses claires et clés **avant** l'envoi au modèle.

Toi, tu continues à tout lire normalement. Les outils retrouvent les vraies valeurs pour travailler.

Ce n'est pas une conformité RGPD. C'est un filet.

## Ce qu'il cache tout seul

- emails
- téléphones français
- IBAN
- adresses du type `12 rue de la Paix` ou `69003 Lyon`
- certaines clés API

## Ce qu'il ne cache pas

Un prénom tout seul. `David`, `Paris`, `Kavyro` restent visibles, sinon l'agent ne comprend plus de qui tu parles.

Pour un nom de client, tu l'ajoutes :

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

Objectif : avant chaque envoi au modèle dans le cloud, masquer emails, téléphones français, IBAN, adresses françaises claires et clés. Je dois continuer à tout lire normalement. Les outils doivent retrouver les vraies valeurs. Ce n'est pas une conformité RGPD.

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
| `/masquer status` | État du mode auto |
| `/masquer on` | Allume le filtre |
| `/masquer off` | Coupe le filtre. Le masquage à la demande marche encore |
| `/masquer ajouter Marie Dupont` | Ajoute un nom à masquer |
| `/masquer liste` | Montre les noms ajoutés |
| `/masquer ...` | Masque un texte collé, ou un fichier (Markdown, PDF) |

## Vérifier

```
/masquer status
/masquer marie.dupont@exemple.fr
```

Le faux email doit ressortir masqué. `On se voit à Paris demain` ne doit pas bouger.

## Limites

- Les noms et adresses vagues ne sont pas devinés.
- Le mapping reste local à ta machine. Ne publie jamais un `/masquer status` qui collerait des vraies valeurs.
- Un PDF se lit si l'outil de lecture PDF est disponible sur la machine. Sinon, passe par le texte ou le Markdown.

## Licence

MIT. Fait pour [Kavyro](https://kavyro.com).
