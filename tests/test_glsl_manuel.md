# Shader GLSL de test — mode « standalone »

Shader GLSL "manuel" classique, à coller dans un onglet de pass pour
vérifier que le mode **GLSL** (📄) s'affiche dans le footer (et non
Shadertoy 🌈), et que le rendu est bien un dégradé rouge/vert animé.

```glsl
void main() {
    vec2 uv = gl_FragCoord.xy / iResolution.xy;
    float pulse = 0.5 + 0.5 * sin(iTime);
    gl_FragColor = vec4(uv.x * pulse, uv.y, 0.2, 1.0);
}
```

**Signaux de détection présents** : `void main()` (signal prioritaire,
`footer.dialect_signal_voidmain`) + `gl_FragColor` (signal secondaire,
ignoré ici car `void main()` est déjà présent).

**Comportement attendu à la compilation** :
- `#version 450` injecté automatiquement (absent du code collé).
- `iResolution`/`iTime` reconnus et injectés seulement parce qu'ils sont
  référencés (bloc `Globals`).
- `gl_FragColor` traduit automatiquement vers une variable `out vec4`
  déclarée pour l'occasion (aucun `out vec4` déclaré à la main ici).

---

## Variante avec `#version` et `out vec4` explicites

Pour vérifier que le code respecte un `#version`/`out vec4` déjà présents
au lieu d'en injecter un second :

```glsl
#version 450
out vec4 fragColor;

void main() {
    vec2 uv = fract(gl_FragCoord.xy / 32.0);
    fragColor = vec4(uv, 0.5, 1.0);
}
```

> ⚠️ Ne référencer ici ni `iResolution`/`iTime`/... ni un `uniform`
> personnalisé : en testant cette variante, j'ai trouvé un vrai bug —
> quand le code a son propre `#version` **et** référence un champ
> `Globals` (`iResolution` par ex.), le bloc `Globals` est injecté
> *avant* le `#version` de l'utilisateur, ce que le compilateur rejette
> (« #version must occur first in shader »). Pas encore corrigé côté
> moteur ; dites-moi si vous voulez que je m'en occupe.

---

## Pour comparaison — shader Shadertoy (mode 🌈, pas GLSL)

```glsl
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / iResolution.xy;
    fragColor = vec4(uv, 0.5 + 0.5 * sin(iTime), 1.0);
}
```
