// Bienvenue dans l'editeur ! Ce code est du GLSL Shadertoy standard, sans
// aucune syntaxe propriétaire : copiez-collez-le tel quel sur shadertoy.com
// et il fonctionnera. Les sliders du panneau du bas sont détectés
// automatiquement à partir des valeurs numériques ci-dessous (essayez de
// bouger "speed" ou "scale") ; déplacer un slider réécrit directement la
// valeur dans le code.

vec3 palette(float t) {
    vec3 a = vec3(0.5, 0.5, 0.5);
    vec3 b = vec3(0.5, 0.5, 0.5);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.263, 0.416, 0.557);
    return a + b * cos(6.28318 * (c * t + d));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    float speed = 1.0;
    float scale = 3.0;
    float colorMix = 0.5;

    vec2 uv = (fragCoord * 2.0 - iResolution.xy) / iResolution.y;
    vec2 uv0 = uv;
    vec3 finalColor = vec3(0.0);

    for (float i = 0.0; i < 4.0; i++) {
        uv = fract(uv * scale) - 0.5;

        float d = length(uv) * exp(-length(uv0));
        vec3 col = palette(length(uv0) + i * 0.4 + iTime * speed * 0.4);

        d = sin(d * 8.0 + iTime * speed) / 8.0;
        d = abs(d);
        d = pow(0.01 / d, 1.2);

        finalColor += col * d;
    }

    vec4 tex = texture(iChannel0, fragCoord / iResolution.xy);
    finalColor = mix(finalColor, finalColor * tex.rgb, colorMix);

    fragColor = vec4(finalColor, 1.0);
}
