// Comprueba que la página del tablero funcione, ANTES de republicarla.
//
//     node docs/check_tablero.js
//
// Por qué existe: el 2026-09-20 se publicó la versión 27 con una llave de cierre de
// menos en el objeto del sprint M.7. El HTML seguía siendo HTML válido, así que se
// publicó sin ruido, pero el `<script>` no parseaba y **la página salía vacía**: sin
// sprints, sin tablas y sin contadores. No hay compilador que lo agarre y la página no
// tiene tests; esto es lo más barato que los reemplaza.
//
// Corre el script de la página contra un `document` de mentira y afirma lo que tiene que
// decir. No dibuja nada: sólo mira el HTML que el script le pasa a cada contenedor.

const fs = require('fs')
const path = require('path')

const archivo = path.join(__dirname, 'TABLERO_FASE_M.html')
const html = fs.readFileSync(archivo, 'utf8')
const script = /<script>([\s\S]*)<\/script>/.exec(html)
if (!script) {
  console.error('FALLA: el HTML no tiene un <script>.')
  process.exit(1)
}

// Lo que el script escribe en cada contenedor, por id.
const salida = {}
const nodo = id => ({
  set innerHTML(v) { salida[id] = v },
  set textContent(v) { salida[id] = v },
  appendChild() {},
  querySelectorAll: () => [],
})
global.document = {
  getElementById: nodo,
  querySelectorAll: () => [],
  createElement: () => ({ dataset: {}, setAttribute() {}, addEventListener() {} }),
}

try {
  eval(script[1])
} catch (e) {
  console.error(`FALLA: el script de la página no corre — la página saldría vacía.\n${e}`)
  process.exit(1)
}

const fallos = []
const esperar = (que, ok) => { if (!ok) fallos.push(que) }
const sprints = salida['sprints'] ?? ''

// Lo estructural, que es lo que rompe una llave mal puesta: si un sprint se come al
// siguiente, esta cuenta baja y nada más se entera.
esperar('los sprints se dibujan (M.0 a M.9, son 10)',
  (sprints.match(/class="sprint"/g) || []).length === 10)
esperar('cada tarea del tablero aparece una vez',
  Number(salida['st-total']) === (sprints.match(/class="tid"/g) || []).length)
esperar('hay una "siguiente tarea"', /M\.\d/.test(salida['siguiente'] ?? ''))
esperar('la ruta marca una sesión como próxima', (salida['ruta'] ?? '').includes('· próxima'))
esperar('la tabla de decisiones no queda vacía', (salida['decisiones'] ?? '').includes('<tr>'))
esperar('las hechas no pasan al total',
  Number(salida['st-hechas']) <= Number(salida['st-total']))

console.log(`tareas: ${salida['st-total']} · hechas: ${salida['st-hechas']} · siguiente: ${(salida['siguiente'] ?? '').replace(/<[^>]+>/g, ' ').trim()}`)

if (fallos.length) {
  console.error('FALLA:\n- ' + fallos.join('\n- '))
  process.exit(1)
}
console.log('El tablero corre y dice lo que tiene que decir.')
