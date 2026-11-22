# Le panneau à côté de Discord

Ce guide fait tourner `superagent panel` sur le VPS où vit Hermes, et l'ouvre depuis le Mac ou le téléphone à travers ton tailnet Tailscale. Rien n'est exposé sur internet : le panneau n'écoute que sur `127.0.0.1` du VPS, et seul ton tailnet y accède par `tailscale serve`.

Remplace `VPS` par le nom de la machine dans ton tailnet et `TAILNET` par le nom du tailnet, tels que `tailscale status` les affiche. N'écris ces noms et le jeton dans aucun fichier du repo.

## 1. Installer le plugin et écrire la config

```bash
hermes plugins install AirKyzzZ/hermes-superagent --enable || hermes plugins update hermes-superagent
mkdir -p ~/superagent && hermes superagent --home ~/superagent setup
hermes superagent --home ~/superagent doctor
```

`setup` écrit `~/superagent/superagent.toml`, en mode 0600, d'après ce que le VPS a : un connecteur pour chaque outil trouvé parmi `gh`, `glab`, `td` et `codexbar`, le tableau des sessions (`agents`) si Claude Code est là, l'envoi des messages par le `hermes` qui la lance, et le fuseau horaire de la machine pour le brief. Elle ne touche jamais à une config qui existe déjà. Pour recevoir les pannes sur un salon Discord, passe `--alerts discord:<id du salon>` dès ce premier lancement, sinon ajoute-le plus tard à la main sous `[channels]`. Elle imprime ensuite la suite, dont, si Claude Code est là, le hook qui met tes sessions sur le tableau. Le tick toutes les 2 minutes qu'elle conseille, c'est l'étape 2.

`doctor` imprime une ligne par vérification et interroge chaque source une fois. À ce stade, des `warn` sont normaux : aucun tick n'a encore tourné, pas de salon d'alertes, pas de heartbeat. Une ligne `fail` est à régler avant de continuer.

```bash
hermes superagent --home ~/superagent panel --json | head -c 300
```

Cette commande doit imprimer le début d'un JSON. Si elle répond « run a tick first », lance `hermes superagent --home ~/superagent tick` une fois, puis réessaie. Ne redémarre pas la gateway, rien ici n'en a besoin.

## 2. Le tick toutes les 2 minutes

Le tick relève les sources et met la base à jour, le panneau ne fait que la lire. Un timer systemd le lance toutes les 2 minutes :

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/superagent-tick.service <<UNIT
[Unit]
Description=hermes-superagent tick

[Service]
Type=oneshot
ExecStart=$(command -v hermes) superagent --home %h/superagent tick
TimeoutStartSec=660
Environment=PATH=$(dirname "$(command -v hermes)"):/usr/local/bin:/usr/bin:/bin
UNIT
cat > ~/.config/systemd/user/superagent-tick.timer <<UNIT
[Unit]
Description=Run the hermes-superagent tick every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now superagent-tick.timer
loginctl enable-linger "$USER"
```

Chaque outil trouvé par `setup` doit être dans ce `PATH`. Si `gh`, `glab` ou `codexbar` vivent ailleurs que dans `/usr/local/bin` ou `/usr/bin`, ajoute leur dossier dans `Environment=PATH=` du service, puis `systemctl --user daemon-reload`. `td` se lance avec `node`, donc il y faut les deux dossiers : `dirname "$(command -v td)"` et `dirname "$(command -v node)"`.

Pour lancer un tick tout de suite par le service et lire son journal :

```bash
systemctl --user start superagent-tick.service
journalctl --user -u superagent-tick -n 5
systemctl --user list-timers superagent-tick.timer
```

Le service tourne avec moins d'environnement que ton shell, donc un identifiant qui marche à la main peut échouer ici. Chaque tick écrit une ligne de JSON dans le journal : `errors` doit être vide, et `warnings` nomme les sources en échec. `list-timers` donne le prochain passage. Après ça, `doctor` ne montre plus `warn tick`.

## 3. Le lancer en service

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

## 4. Le rendre accessible dans le tailnet, et seulement là

`tailscale serve` demande root, sauf si ton utilisateur est opérateur de Tailscale. La première ligne le fait une fois pour toutes, ensuite plus besoin de `sudo` :

```bash
sudo tailscale set --operator=$USER
tailscale serve --bg 8765
tailscale serve status
```

`status` doit montrer `https://VPS.TAILNET.ts.net` qui renvoie vers `http://127.0.0.1:8765`, sans Funnel. N'utilise jamais `tailscale funnel` : il publierait le panneau sur internet, et seul le jeton te protégerait.

Si le port 443 est déjà pris sur le VPS, par un autre site ou un autre `tailscale serve`, sers le panneau sur son propre port :

```bash
tailscale serve status
tailscale serve --bg --https=8765 http://127.0.0.1:8765
```

`status` montre d'abord ce qui est déjà servi, pour ne rien écraser. Garde le même port des deux côtés : le panneau accepte `VPS.TAILNET.ts.net:8765` parce que 8765 est son port, un autre port donnerait un `421`. L'adresse devient `https://VPS.TAILNET.ts.net:8765/#JETON`. Le journal du service l'affiche sans `:8765`, ajoute-le à la main. Pour retirer ce partage : `tailscale serve --https=8765 off`.

Pour vérifier depuis le Mac que la porte est fermée sans jeton :

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://VPS.TAILNET.ts.net/api/snapshot
```

La réponse attendue est `403`. Si tu sers sur 8765, mets `https://VPS.TAILNET.ts.net:8765/api/snapshot`.

## 5. Le jeton

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

## 6. L'ouvrir à côté de Discord

- **Sur le Mac :** ouvre `https://VPS.TAILNET.ts.net/#JETON` dans une fenêtre de navigateur que tu colles à côté de Discord, ou `https://VPS.TAILNET.ts.net:8765/#JETON` si tu sers sur 8765. Avec cmux, ouvre un split navigateur à droite du terminal et colle la même URL. La page est faite pour 420 à 560 px de large et passe sur deux colonnes au-delà de 900 px.
- **Sur le téléphone :** même URL dans le navigateur, avec l'app Tailscale connectée.
- **Sans Tailscale :** `ssh -N -L 8765:127.0.0.1:8765 VPS` sur le Mac, puis `http://127.0.0.1:8765/#JETON`.

Le compteur dans le titre de l'onglet, par exemple `(3) superagent`, te dit combien de choses t'attendent, même quand l'onglet est en arrière-plan.

## Ce que tu vois en mode shadow

Sans rôle posé dans le profil, il n'y a ni router ni workers. Le panneau montre quand même le ledger, les sessions Claude Code de tes machines (connecteurs `agents`), la santé des sources et ce qui t'attend parmi elles. Les cartes projet se remplissent avec tes sessions et les repos de tes issues et PRs. Les workers et leurs conversations apparaissent une fois les rôles `router`, `worker` et `watchdog` posés.

## Si ça ne marche pas

| Ce que tu vois | Ce qu'il faut faire |
| --- | --- |
| `superagent.toml: [Errno 2] No such file or directory`, ou « no superagent.toml » | La config n'existe pas encore : lance `setup`, étape 1 |
| `421` ou « open the panel on 127.0.0.1 » | Le nom dans `--allow-host` ne correspond pas exactement à celui de l'URL |
| `421` sur chaque requête, avec un port dans l'URL | Tailscale sert sur un autre port que celui du panneau. Sers sur le même, étape 4 |
| « Wrong or missing token » | Il manque le `#JETON` à la fin de l'URL, ou le jeton a été changé |
| « run a tick to update it » | La base date d'une version plus ancienne, un `tick` la met à jour |
| `502` de Tailscale | Le service est arrêté : `systemctl --user status superagent-panel` |

## Voir les sessions du Mac et du PC

Le tableau des agents lit les sessions Claude Code des autres machines par ssh depuis le VPS, à travers le tailnet. Le détail et les raisons sont dans [Sessions on other machines](dogfood-install.md#sessions-on-other-machines), avec les blocs de config. Ci-dessous, `mac`, `pc` et `you` sont à remplacer par tes noms, dans `~/.ssh/config` du VPS seulement.

1. **Sur le Mac :** active Session à distance (Réglages Système > Général > Partage) pour ton utilisateur seulement, et vérifie que `/usr/bin/python3 --version` répond. S'il propose les outils de ligne de commande, installe-les. Pour que superagent puisse relancer une session dans cmux, mets Réglages cmux > Automation > Socket Control Mode sur « Automation mode ».
2. **Sur le PC Windows, dans un PowerShell administrateur :** installe et démarre le serveur OpenSSH de Windows (Tailscale SSH ne tourne pas sur Windows), puis vérifie Python et git :

   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Set-Service sshd -StartupType Automatic; Start-Service sshd
   python --version; git --version
   ```

   Si ton compte est administrateur, sshd lit la clé du VPS dans un fichier partagé sous `C:\ProgramData\ssh`, pas dans ton profil. Crée d'abord la clé sur le VPS (étape 3) et affiche-la avec `cat ~/.ssh/superagent.pub`. Les étapes « administrative user » du [guide de Microsoft](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement#administrative-user) envoient une clé depuis un client Windows : sur le PC, lance seulement les commandes `Add-Content` et `icacls` de la chaîne `$remotePowershell`, avec la clé du VPS en `-Value` et chaque `""` écrit `"`. Sur un Windows en français, remplace `Administrators` et `SYSTEM` dans la ligne `icacls` par `*S-1-5-32-544` et `*S-1-5-18`. Une fois la connexion par clé vérifiée, mets `PasswordAuthentication no` dans `C:\ProgramData\ssh\sshd_config` puis `Restart-Service sshd`.

   Claude Code a besoin de Git pour Windows : il lance les hooks dans Git Bash, et sans lui dans PowerShell, où le hook ne démarre pas et bloque chaque prompt. Dans `~/.ssh/config` du VPS, `User` est ton nom d'utilisateur Windows local (`$env:USERNAME`), pas l'email d'un compte Microsoft. Sur Windows, superagent ne voit pas si un `claude` tourne encore, et ne peut pas relancer une session : laisse `relaunch` désactivé pour le PC.
3. **Sur le VPS, par Hermes :** crée la clé et les alias `mac` et `pc` comme dans le guide, copie la clé sur le Mac avec `ssh-copy-id -o StrictHostKeyChecking=accept-new -i ~/.ssh/superagent.pub mac` (cette ligne demande ton mot de passe du Mac, lance-la toi-même dans un terminal, pas par Hermes), enregistre la clé d'hôte de chaque machine et pose le hook avec les commandes du guide. Hermes fusionne ensuite le bloc imprimé par `hermes superagent agents --hook-settings` dans `~/.claude/settings.json` du Mac, et le même bloc avec `python` au lieu de `python3` dans `%USERPROFILE%\.claude\settings.json` du PC, en te montrant le diff.

   ```bash
   ssh -o StrictHostKeyChecking=accept-new mac /usr/bin/python3 --version
   echo 'print(1)' | ssh -o StrictHostKeyChecking=accept-new pc python -    # doit afficher 1
   ```

4. Ajoute les blocs `agents-mac` et `agents-pc` du guide à `~/superagent/superagent.toml`, puis lance `hermes superagent --home ~/superagent doctor`. Chaque machine doit afficher `ok` suivi de `N items`, ou `warn` tant qu'aucune session n'a eu d'événement depuis la pose du hook. Un `fail` dit pourquoi et donne la commande ssh à essayer à la main.
