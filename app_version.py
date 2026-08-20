"""Version affichée dans le titre de la fenêtre et la boîte "A propos".

A tenir synchronisée à la main avec `version` dans `rust_engine/Cargo.toml`
(et son `Cargo.lock`, entrée du paquet `shadertoy_engine` lui-même — pas les
dépendances tierces), `version` dans `rust_engine/pyproject.toml`, et
`#define AppVersion` dans `packaging/installer.iss` (voir la section 5 de
COMPILATION.md) : ce sont les quatre seuls endroits du projet où le numéro
de version apparaît. (Historique : `Cargo.toml`/`pyproject.toml` étaient
restés bloqués à `0.1.0` jusqu'à la synchronisation faite en 0.1.6 — vérifier
les quatre à chaque bump plutôt que de supposer qu'ils suivent déjà.)
"""
APP_VERSION = "0.1.18"
