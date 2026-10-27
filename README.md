# Tod4S3

## Qué hace

NikuTodusS3 es un bot de Telegram que recibe archivos o enlaces públicos, los sube a ToDus S3 y devuelve enlaces de descarga. Incluye cola de tareas, historial y controles para pausar o reanudar. El bot funciona en chats privados y muestra enlaces opcionales al canal oficial `@Dark_Zone_666`, al grupo de soporte `@grupozonaoscura` y al canal ToDusS3 `@darzPrbo`.

Unirse a esos canales es opcional: el botón **Continuar al bot** abre el menú sin consultar membresías. Los enlaces a archivos en la nube caducan a las **2 horas** para todos los planes.

La subida a ToDus usa un `PUT` directo: no necesita cuenta, teléfono ni contraseña de ToDus.

Las descargas de plataformas se limitan a contenido accesible públicamente. Usa el bot solo con archivos o medios que tengas derecho a descargar y compartir, respetando las leyes y las condiciones de cada servicio. El bot no carga cookies ni intenta acceder a publicaciones privadas, eludir inicios de sesión o saltar DRM o restricciones de plataforma.

## Instalación local

Necesitas Docker con Docker Compose y un token creado con [@BotFather](https://t.me/BotFather).

```bash
git clone https://github.com/niku-source/Tod4S3.git
cd Tod4S3
cp .env.example .env
```

Edita `.env` y configura, como mínimo, `TELEGRAM_BOT_TOKEN`. Para activar el respaldo, configura también `TELEGRAM_BACKUP_CHAT_ID` como se explica abajo. Si recibirás reenvíos de Telegram mayores de 20 MiB, agrega `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` obtenidos en [my.telegram.org](https://my.telegram.org); no son el token de BotFather.

```bash
docker compose up -d --build
docker compose logs -f
```

Abre el bot, ejecuta `/start` y prueba primero con un archivo pequeño. `.env` está excluido de Git: nunca subas tokens, hashes, cookies ni sesiones al repositorio.

## Configurar el respaldo cifrado

1. Crea un grupo o canal privado para las copias y agrega el bot como administrador. Dale permiso para publicar y fijar mensajes; también debe poder editar o borrar su copia anterior.
2. En `.env` o en las variables de Railway, configura:

   ```env
   TELEGRAM_BACKUP_CHAT_ID=-1001234567890
   TELEGRAM_BACKUP_INTERVAL_SECONDS=300
   BOT_DATA_DIR=/data
   ```

   Sustituye el ID de ejemplo por el ID numérico real del chat, o usa su `@usuario` público.
3. En Railway, monta un Volume en la ruta `/data`. En la instalación Docker local, Compose crea los volúmenes persistentes `bot-data` y `bot-tmp`.
4. Reinicia el servicio y comprueba los logs. El comando `/backup`, disponible para el dueño, fuerza una copia inmediata.

El bot restaura primero la copia fijada y luego inicia MTProto. Guarda automáticamente al arrancar, después de cambios, cada 300 segundos por defecto y al cerrar ordenadamente. La copia incluye los datos de planes asignados por administración, uso, historial, usuarios y, si existe, la sesión MTProto. El archivo se cifra con AES-256-GCM y la clave se deriva de `TELEGRAM_BOT_TOKEN`; conserva el mismo token para poder restaurar copias existentes. La copia cifrada debe mantenerse por debajo de 20 MiB para que la Bot API pueda descargarla al restaurar.

## Variables principales

- `TELEGRAM_BOT_TOKEN`: obligatorio; se obtiene de @BotFather.
- `TELEGRAM_BACKUP_CHAT_ID`: chat donde se publica y fija la copia.
- `TELEGRAM_BACKUP_INTERVAL_SECONDS`: intervalo automático; predeterminado 300 segundos, mínimo 30.
- `BOT_DATA_DIR`: ruta de datos persistentes; usa `/data` y monta un volumen en esa ruta.
- `TELEGRAM_API_ID` y `TELEGRAM_API_HASH`: opcionales; necesarios para que MTProto descargue reenvíos grandes de Telegram. Se obtienen en my.telegram.org y no sustituyen el token del bot.
- `MAX_FILE_SIZE_MIB`: máximo de entrada; predeterminado 4096 MiB.
- `MAX_PART_SIZE_MIB`: tamaño de cada parte; predeterminado 1900 MiB. Los archivos mayores se dividen en partes con enlaces separados.
- `MAX_TRANSFER_HOURS`: tiempo máximo para una transferencia; predeterminado 24 horas.

## Saldo y referidos

Cada usuario recibe al registrarse **1 GB de datos gratis** y **5$ de saldo virtual**. Cada referido válido añade 5$ al saldo del invitador, una sola vez. Cuando se agota la cuota, el usuario puede comprar desde **Saldo y planes** usando ese saldo virtual (no se procesa dinero real): 500 MB por 5$, 1 GB por 10$, 2 GB por 20$, 3 GB por 30$, 5 GB por 50$ o 20 GB por 200$.

Los paquetes se acumulan, no vencen por tiempo y solo se descuentan cuando termina correctamente una transferencia. El botón de saldo muestra el enlace personal, el saldo, los paquetes y sus acciones de compra dentro del mismo mensaje. Los roles VIP que asigne un administrador siguen siendo independientes de estos paquetes de datos.

## Límites de archivos

La Bot API pública solo permite descargar archivos de hasta 20 MiB con `getFile`; para reenvíos mayores se necesita MTProto. Telegram limita el tamaño de los archivos adjuntos a 2 GB, o 4 GB con Premium ([FAQ de Telegram](https://telegram.org/faq#q-what-can-i-send-my-friends), [Bot API](https://core.telegram.org/bots/api#file)). Por eso, un adjunto de Telegram de 100 GB no es posible.

El máximo predeterminado de este bot es 4096 MiB. Subirlo a 100 GB no basta para procesar un enlace de ese tamaño: el flujo actual guarda el archivo temporal y crea copias por partes, por lo que necesita cerca de 200 GB de espacio libre durante el procesamiento. No aumentes `MAX_FILE_SIZE_MIB` a 100 GB hasta contar con almacenamiento suficiente y comprobar el límite del proveedor.

## Despliegue en Railway

Crea un servicio desde `https://github.com/niku-source/Tod4S3` en la rama `main`; Railway usa `Dockerfile` y `railway.json`. En **Variables**, configura `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BACKUP_CHAT_ID` y `BOT_DATA_DIR=/data`. Monta un Volume con ruta `/data`. Añade `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` solo si el bot debe descargar reenvíos grandes de Telegram. Guarda los cambios y espera el redeploy.

Si aparece el aviso de directorio **NO PERSISTENTE**, el volumen no está montado en `/data` o `BOT_DATA_DIR` apunta a otra ruta; los datos locales podrían perderse al redesplegar.
