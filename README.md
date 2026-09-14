# ChartScript

Convierte un MIDI ya transcrito en una carpeta de cancion de **Clone Hero**,
editable en **Moonscraper**. Arrastras el MIDI a `Hacer chart.bat` y eliges que
pista del MIDI va a que canal del chart.

**No transcribe.** Da por hecho que el MIDI ya existe (Mirelo AI, un DAW, lo que
sea). Resuelve la otra mitad del problema, que es la que ninguna herramienta de
transcripcion toca: encajar esas notas en el tempo real de la grabacion,
reducirlas a cinco carriles y empaquetar la carpeta que el juego escanea.

Windows. Python 3.11 o 3.12, y ffmpeg.

---

## Uso

### 1. Instalar (una vez)

```bash
git clone https://github.com/Gallebot/ChartScript.git
```

Doble clic en **`Instalar.bat`**: crea el entorno de Python e instala las
dependencias de `requirements.txt`. Nada de torch ni modelos pesados.

Aparte hace falta **ffmpeg** en el PATH. Sin el no se puede generar `song.ogg`:

```bash
winget install Gyan.FFmpeg
```

Abre una consola nueva despues de instalarlo, o no vera el PATH actualizado.

### 2. Poner el audio al lado del MIDI

Con el mismo nombre:

```
D:\cosas\ARTISTA - CANCION.mid
D:\cosas\ARTISTA - CANCION.mp3
```

No es obligatorio, pero cambia mucho el resultado: la razon esta abajo en
"De donde sale el tempo".

### 3. Arrastrar el MIDI a `Hacer chart.bat`

Te muestra las pistas del archivo:

```
Pistas del MIDI:
   1  voice                        689 notas   se deduce: vocals (ningun backend lo escribe)
   2  electric_piano               387 notas   se deduce: keys
   3  electric_bass                339 notas   se deduce: bass
   4  violin                       112 notas   sin clasificar
   5  synth_pad                     61 notas   se deduce: keys
   6  drums                        526 notas   se deduce: drums
```

Y te deja elegir entre tres modos:

- **[1] Asignacion deducida** — una carpeta, con lo que el clasificador adivino
  por el nombre de cada pista.
- **[2] A mano** — para cada canal del chart escribes los numeros de las pistas
  que van dentro. Varias pistas en un canal se **suman** (asi se juntan dos
  teclados en `keys`).
- **[3] Variantes** — para cada canal escribes varios **candidatos** y sale una
  carpeta por combinacion.

El modo 3 es el que responde a "no se si la guitarra deberia ser la voz o el
bajo". Escribes `guitar > 1,3` y salen dos carpetas, una con cada cosa. Se juegan
y se decide con el mando en la mano en vez de razonandolo.

### 4. Copiar el resultado

Cada carpeta de `out\` esta completa: `notes.chart`, `song.ini`, `song.ogg` y
`album.png`. Se copia tal cual a la carpeta de canciones de Clone Hero.

Para editarla, se abre su `notes.chart` en Moonscraper.

---

## De donde sale el tempo

Es la decision que mas afecta al resultado, asi que conviene entenderla.

### Con audio al lado del MIDI

El mapa de tempo se **ajusta a la grabacion**: se detectan los beats del audio y
se construye un `[SyncTrack]` que los sigue. El tempo que trae el MIDI se
**ignora a proposito**.

Suena raro tirar informacion, pero el motivo es medido: en el MIDI de prueba el
archivo traia 7 eventos de tempo y la deteccion sobre la grabacion saco 74. Una
grabacion real respira —acelera en los estribillos, cede en los puentes— y un
chart montado sobre un tempo plano encaja al principio y se despega al minuto.

### Sin audio

El tempo se toma del propio MIDI, y `song.ogg` sale **en silencio**.

El chart se puede abrir y editar en Moonscraper, pero no jugar con musica. Sirve
para ver la estructura y corregir notas; no para validar el timing, porque no hay
nada contra lo que validarlo.

---

## Que hace con cada canal

| canal del chart | como se convierte |
|---|---|
| `guitar` `bass` `rhythm` `coop` `keys` | Frontend melodico: cuantizacion contra la rejilla, reduccion de alturas a cinco carriles, acordes, sustains, HOPOs y frases de Star Power. Los umbrales estan calibrados sobre 192 charts humanos, no elegidos a ojo. |
| `drums` | Percusion General MIDI -> carriles y marcadores de platillo. Sin sustains ni HOPO: en la coleccion de referencia los charts de bateria no llevan **ningun** sustain y hay **un solo** flag de force en total. |

`vocals` no se ofrece: necesita una pista de voces en el `.mid` que ningun backend
de aqui genera todavia. Si el MIDI trae una pista de voz y la quieres jugable,
asignala a `guitar` o a `keys`.

### Ojo con la bateria

El criterio de rejilla de la bateria **no** es el mismo que el de las lineas
melodicas, y la diferencia fue un bug real. El criterio melodico elige la rejilla
mas gruesa que explique un 80% de los ataques; aplicado a la bateria del MIDI de
prueba eligio corcheas y dejo **99 de 526 golpes fuera**. Un bombo que falta se
nota al jugar; uno 20 ms desplazado, no. Asi que la bateria afina la rejilla hasta
perder menos del 5%.

Si en el informe ves un porcentaje alto de golpes descartados, el problema casi
siempre esta antes: un mapa de tempo que no encaja. Medido sobre la misma
bateria: con el tempo del MIDI, 0 descartes; con un tempo del audio mal ajustado,
8%.

---

## Formato: `.chart` o `.mid`

Por defecto se escribe **`notes.chart`**, y es la opcion sensata: Clone Hero lo
juega y Moonscraper lo edita.

Se puede pedir `both` en la configuracion de `Hacer chart.bat`, pero con los dos
archivos presentes **Clone Hero se queda con el `.mid` e ignora el `.chart`**. Es
decir: editarias el `.chart` y no cambiaria nada de lo que suena. `notes.mid` solo
hace falta para cosas que el `.chart` no sabe representar (voces, Pro Drums) o
para abrirlo en herramientas de la familia Rock Band.

---

## Linea de comandos

El `.bat` solo recibe el arrastre y llama a `hacer_chart.py`. Para repetir algo ya
decidido, o para scripts, se puede llamar directo:

```bash
# Ver las pistas y salir
python hacer_chart.py "x.mid" --list

# Una carpeta, asignacion explicita
python hacer_chart.py "x.mid" --assign "voice=guitar,electric_bass=bass,drums=drums"

# Una carpeta por combinacion
python hacer_chart.py "x.mid" --candidates "guitar=voice|electric_bass,bass=electric_bass,drums=drums"
```

En `--assign` y `--candidates` una pista se puede nombrar por su nombre, por su
indice (el numero que sale en `--list`) o por el instrumento que se le dedujo.

Opciones que importan:

| opcion | para que |
|---|---|
| `--allow-reuse` | Deja que la misma pista caiga en dos canales. Por defecto esas combinaciones se descartan **y se cuentan**, para que no desaparezcan en silencio. |
| `--max-variants N` | Tope de carpetas. Por defecto 24. |
| `--bpm-hint N` | Si la deteccion de beats se engancha al doble de tempo. Es el parametro que mas arregla cuando el chart sale a mitad de velocidad. |
| `--subdivision N` | Fuerza la rejilla en vez de dejar que se elija. |
| `--sections` | Detecta Intro/Verse/Chorus del audio. Los limites son fiables; los nombres son una heuristica. |
| `--lyrics x.lrc` | Letra sincronizada. |
| `--force` | Pisa una carpeta que ya exista. |

`python hacer_chart.py --help` tiene el resto.

---

## Cuando algo sale mal

**Primero descarta que el problema sea el pipeline.** Doble clic en
**`Verificar timing.bat`**: genera un chart de calibracion (una nota por negra
sobre un metronomo) y lo mide comparando los onsets del audio con los ticks del
`.chart`, parseando el `.chart` desde cero sin usar el propio `TempoMap`.

Si eso pasa, la base esta bien y el problema esta en el MIDI o en la deteccion de
beats. Lo prueba a dos BPM, uno redondo y `174.267`, que es el que delata errores
de cuantizacion del evento `B` (guarda BPM×1000 como entero).

Lo que importa de la medida no es el error absoluto —Vorbis mete un sesgo de unos
2 ms en el ataque— sino la **deriva**: un error de BPM crece a lo largo de la
cancion, un artefacto del codec no.

| sintoma | donde mirar |
|---|---|
| El chart se despega a medida que avanza | Mapa de tempo. Mira el numero de "beats fuera de presupuesto" en el informe; si es alto, prueba `--bpm-hint`. |
| Todo suena a la mitad o al doble de velocidad | `--bpm-hint` con el tempo real. |
| Faltan muchos golpes de bateria | El porcentaje de descartados en el informe. Casi siempre es el tempo, no la bateria. |
| La cancion se ve vacia en el juego | No hay canal `guitar`. Clone Hero abre en guitarra; hay que cambiar de instrumento en el juego, o asignar algo a `guitar`. |
| "Ya existe una carpeta con ese nombre" | Otra variante o un chart anterior. `REEMPLAZAR=1` en el `.bat`, o borra la carpeta. |
| Densidad "por encima del 90% de los charts humanos" | Sobran notas: el MIDI trae mas de lo que escribiria una persona. No es un error del pipeline y no hay filtro que lo arregle bien —un MIDI no dice que notas importan mas—, asi que la salida es elegir una pista mas simple para ese canal, o adelgazarlo en Moonscraper. |
| `No encuentro un entorno de Python` | Falta ejecutar `Instalar.bat`. |
| `No se encontro 'ffmpeg' en el PATH` | `winget install Gyan.FFmpeg`, y abre una consola nueva. |

---

## Que hay dentro

```
ChartScript/
  Hacer chart.bat          arrastra un MIDI aqui
  Verificar timing.bat     calibracion: comprueba que la base esta bien
  Instalar.bat             crea el entorno de Python (una vez)

  hacer_chart.py           punto de entrada y CLI
  tui.py                   la interfaz de consola
  pipeline.py              el nucleo: prepare() una vez, build_variant() N veces
  variants.py              combinatoria de asignaciones
  calibrar.py              genera el chart de calibracion

  chartgen/                la libreria: IR, tempo, charting, backends y frontends
  tools/                   verificacion y medida contra charts de referencia
  tests/                   258 tests
  out/                     las carpetas generadas (ignorada por git)
```

Cada modulo de `chartgen/` lleva en su docstring el porque de sus decisiones y la
medicion que las respalda. Si vas a tocar algo, empieza por ahi.

### Tests

```bash
.venv\Scripts\python -m pytest tests -q
```

257 en unos 15 s. El unico que paga deteccion de beats esta marcado `slow` y
deseleccionado por defecto; para incluirlo:

```bash
.venv\Scripts\python -m pytest tests -q -m slow
```

### Por que Python y no todo en `.bat`

Lo que hay que hacer en la interfaz es: mostrar una lista de longitud desconocida,
dejar elegir varios elementos por canal, multiplicar las elecciones y confirmar.
En `cmd` eso significa variables numeradas a mano (`PISTA1`, `PISTA2`...), `for /f`
sobre la salida de otro proceso y `enabledelayedexpansion` dentro de bucles
anidados. Cada una de esas tres cosas tiene su forma de romperse en silencio, y de
hecho la primera version de `Verificar timing.bat` ya tenia dos: una colision de
variable en un `for` anidado y una ruta partida en dos porque `delims=:` tambien
corta el `C:` de la unidad.

El `.bat` se queda en lo que hace bien: recibir el arrastre y llamar a Python.

### De donde sale `chartgen/`

Es el nucleo de un proyecto mas grande que ademas transcribe audio (Demucs +
pYIN) y convierte tablaturas de Guitar Pro. Aqui esta solo la parte que este flujo
necesita, calculada por cierre de imports y no a ojo.

Lo que se dejo fuera a proposito: `cli.py`, `download.py`, `separate.py` (Demucs)
y `frontends/guitarpro.py`. Son la ruta de transcribir audio uno mismo y la de
partir de una tablatura, y ninguna de las dos participa aqui. `separate.py` esta
unicamente porque `tools/benchmark.py` lo importa.

Consecuencia practica: estos modulos son una **copia**. Un arreglo aqui no llega
al proyecto de origen ni al reves, y no hay nada que avise de la divergencia.

### Nota para quien clone

Los `.bat` buscan el entorno de Python en `.venv\` y, si no esta, en `..\.venv\`.
Ese segundo sitio es como esta montado en la maquina donde se desarrolla; en un
clon normal no existe y se usa el primero, que es el que crea `Instalar.bat`.
