# Le panneau à côté de Discord

Ce guide fait tourner `superagent panel` sur le VPS où vit Hermes, et l'ouvre depuis le Mac ou le téléphone à travers ton tailnet Tailscale. Rien n'est exposé sur internet : le panneau n'écoute que sur `127.0.0.1` du VPS, et seul ton tailnet y accède par `tailscale serve`.

Remplace `VPS` par le nom de la machine dans ton tailnet et `TAILNET` par le nom du tailnet, tels que `tailscale status` les affiche. N'écris ces noms et le jeton dans aucun fichier du repo.

## 1. Mettre le plugin à jour sur le VPS

```bash
hermes plugins install AirKyzzZ/hermes-superagent --enable || hermes plugins update hermes-superagent
hermes superagent --home ~/superagent panel --json | head -c 300
```

La deuxième commande doit imprimer le début d'un JSON. Si elle répond « run a tick first », lance `hermes superagent --home ~/superagent tick` une fois, puis réessaie. Ne redémarre pas la gateway, rien ici n'en a besoin.

## 2. Le lancer en service

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/superagent-panel.service <<UNIT
[Unit]
Description=hermes-superagent side panel

[Service]
ExecStart=$(command -v hermes) superagent --home %h/superagent panel --allow-host VPS.TAILNET.ts.net
Restart=on-failure
Environment=PATH=$(dirname "$(command -v hermes)"):/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now superagent-panel.service
loginctl enable-linger "$USER"
sleep 1; journalctl --user -u superagent-panel -n 5
```

Le journal affiche l'URL locale et l'URL Tailscale, chacune suivie du jeton après le `#`.

## 3. Le rendre accessible dans le tailnet, et seulement là

```bash
tailscale serve --bg 8765
tailscale serve status
```

`status` doit montrer `https://VPS.TAILNET.ts.net` qui renvoie vers `http://127.0.0.1:8765`, sans Funnel. N'utilise jamais `tailscale funnel` : il publierait le panneau sur internet, et seul le jeton te protégerait.

Pour vérifier depuis le Mac que la porte est fermée sans jeton :

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://VPS.TAILNET.ts.net/api/snapshot
```

La réponse attendue est `403`.

## 4. Le jeton

Il est dans `~/superagent/.superagent.panel-token` sur le VPS, en mode 0600, et reste le même d'un redémarrage à l'autre, donc le lien peut aller dans tes favoris. Pour l'afficher :

```bash
cat ~/superagent/.superagent.panel-token
```

S'il a fuité, supprime-le et redémarre le service, qui en crée un nouveau. Les anciens liens arrêtent alors de marcher :

```bash
rm ~/superagent/.superagent.panel-token
systemctl --user restart superagent-panel.service
sleep 1; journalctl --user -u superagent-panel -n 5
```

## 5. L'ouvrir à côté de Discord

- **Sur le Mac :** ouvre `https://VPS.TAILNET.ts.net/#JETON` dans une fenêtre de navigateur que tu colles à côté de Discord. Avec cmux, ouvre un split navigateur à droite du terminal et colle la même URL. La page est faite pour 420 à 560 px de large et passe sur deux colonnes au-delà de 900 px.
- **Sur le téléphone :** même URL dans le navigateur, avec l'app Tailscale connectée.
- **Sans Tailscale :** `ssh -N -L 8765:127.0.0.1:8765 VPS` sur le Mac, puis `http://127.0.0.1:8765/#JETON`.

Le compteur dans le titre de l'onglet, par exemple `(3) superagent`, te dit combien de choses t'attendent, même quand l'onglet est en arrière-plan.

## Ce que tu vois en mode shadow

Sans rôle posé dans le profil, il n'y a ni router ni workers. Le panneau montre quand même le ledger, les sessions Claude Code de tes machines (connecteurs `agents`), la santé des sources et ce qui t'attend parmi elles. Les cartes projet se remplissent avec tes sessions et les repos de tes issues et PRs. Les workers et leurs conversations apparaissent une fois les rôles `router`, `worker` et `watchdog` posés.

## Si ça ne marche pas

| Ce que tu vois | Ce qu'il faut faire |
| --- | --- |
| `421` ou « this panel doesn't answer to … » | Le `Host` cité entre guillemets n'est pas accepté : passe son nom à `--allow-host`, avec `:port` seulement pour n'accepter que ce port |
| « Wrong or missing token » | Il manque le `#JETON` à la fin de l'URL, ou le jeton a été changé |
| « run a tick to update it » | La base date d'une version plus ancienne, un `tick` la met à jour |
| `502` de Tailscale | Le service est arrêté : `systemctl --user status superagent-panel` |
