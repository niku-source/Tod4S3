# Tod4S3 Sync

Cliente Android inicial para Tod4S3. La aplicación no contiene la contraseña de ToDus ni el token del bot.

## Vinculación

1. Instala la aplicación.
2. En el chat privado del bot envía `/app`.
3. Escribe en la aplicación el código de un solo uso recibido.
4. El código caduca en 10 minutos y se intercambia por un token del dispositivo.

## Descargas

La aplicación consulta las entregas del usuario cada 15 segundos mientras está abierta. Cada entrega contiene uno o varios enlaces ToDus. Si existen varias partes, las descarga en orden y las concatena en un único archivo dentro del almacenamiento privado de la aplicación, en `Download`.

## API usada

- `POST /api/app/pair`: intercambia código y `device_id` por token.
- `GET /api/app/deliveries`: devuelve las entregas del usuario autenticado.
- `POST /api/app/revoke`: revoca el token del dispositivo.

## Estado

Esta es la primera base funcional. La siguiente iteración puede añadir notificaciones push, descarga en segundo plano, destino público de Descargas y botón de cerrar sesión.
