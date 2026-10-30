# Revisión de límites de Discord

Discord documenta límites por ruta, recurso y globales, y recomienda interpretar los encabezados de respuesta y respetar `retry_after` al recibir HTTP 429. Los límites pueden cambiar y no existe un valor universal garantizado. [Documentación oficial](https://docs.discord.com/developers/topics/rate-limits).

La [política de desarrolladores](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy) prohíbe eludir los límites y exige consentimiento escrito para superar los límites autorizados. Los intentos fallidos repetidos también pueden provocar restricciones temporales.

Se revisaron proyectos que anuncian rotación de proxies, entre ellos [ClaimCord](https://github.com/tk4ze/ClaimCord). Sus afirmaciones no demuestran disponibilidad ni ausencia de límites; la rotación para sortear un 429 no se incorpora a esta aplicación. Otro ejemplo consultado interpreta un 404 del endpoint de usuarios como nombre disponible, algo que no constituye una comprobación válida de nombres. La consulta de usuarios de este proyecto sigue dependiendo de un endpoint no documentado y sus respuestas requieren confirmación en Discord.

Los cambios de esta versión se enfocan en conservar las esperas, detener solicitudes rechazadas y mostrar resultados inciertos como tales. No se ha probado que Discord acepte ninguna frecuencia específica ni se garantizan solicitudes sin límites.
