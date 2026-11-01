package com.nmr.ytscheduler.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("ytscheduler", Context.MODE_PRIVATE)
    var server: String
        get() = sp.getString("server", "") ?: ""
        set(v) = sp.edit().putString("server", v.trim().trimEnd('/')).apply()
    var password: String
        get() = sp.getString("password", "") ?: ""
        set(v) = sp.edit().putString("password", v.trim()).apply()
    var seen: Set<String>
        get() = sp.getStringSet("seen", null) ?: emptySet()
        set(v) = sp.edit().putStringSet("seen", v).apply()
    var seeded: Boolean
        get() = sp.getBoolean("seeded", false)
        set(v) = sp.edit().putBoolean("seeded", v).apply()
    val configured get() = server.isNotBlank() && password.isNotBlank()
}

data class Channel(
    val id: Int, val email: String?, val channelId: String?, val title: String?,
    val isStorage: Boolean, val hasDrive: Boolean, val needsReconnect: Boolean,
)

data class Playlist(val id: String, val title: String)

data class Video(
    val id: Int, val status: String, val title: String?, val description: String,
    val tags: List<String>, val categoryId: String?, val madeForKids: Boolean, val synthetic: Boolean,
    val playlistId: String?, val channelAccountId: Int?, val channel: String?,
    val publishAt: Instant?, val postedAtLocal: String?, val fileName: String?, val fileSize: Long,
    val youtubeUrl: String?, val hasThumbnail: Boolean, val error: String?,
) {
    val displayTitle get() = title?.takeIf { it.isNotBlank() } ?: fileName ?: "Untitled"
}

class ApiException(val code: Int, message: String) : Exception(message)

private fun JSONObject.str(k: String): String? = if (!has(k) || isNull(k)) null else getString(k)
private fun JSONObject.int(k: String): Int? = if (!has(k) || isNull(k)) null else getInt(k)

class Api(ctx: Context) {
    private val prefs = Prefs(ctx)
    val base get() = prefs.server

    val authHeaders: Map<String, String>
        get() = mapOf("Authorization" to "Bearer ${prefs.password}", "ngrok-skip-browser-warning" to "1")

    private suspend fun request(
        method: String, path: String, json: JSONObject? = null,
        raw: ByteArray? = null, contentType: String? = null,
    ): String = withContext(Dispatchers.IO) {
        val conn = URL(base + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 20000
            conn.readTimeout = 60000
            authHeaders.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            val body = json?.toString()?.toByteArray() ?: raw
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", contentType ?: "application/json")
                conn.outputStream.use { it.write(body) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) {
                val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                val msg = when {
                    code == 401 -> "Wrong app password"
                    !detail.isNullOrBlank() -> detail
                    text.contains("ngrok", true) && code >= 500 -> "Server is offline (is the PC on?)"
                    else -> "Server error $code"
                }
                throw ApiException(code, msg)
            }
            text
        } catch (e: java.io.IOException) {
            throw ApiException(0, "Can't reach the server. Is the PC on and online?")
        } finally {
            conn.disconnect()
        }
    }

    suspend fun health(): JSONObject = JSONObject(request("GET", "/api/health"))

    suspend fun channels(): List<Channel> {
        val arr = JSONArray(request("GET", "/api/channels"))
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            Channel(
                o.getInt("id"), o.str("email"), o.str("channel_id"), o.str("channel_title"),
                o.optBoolean("is_storage"), o.optBoolean("has_drive"), o.optBoolean("needs_reconnect"),
            )
        }
    }

    suspend fun playlists(accountId: Int): List<Playlist> {
        val arr = JSONArray(request("GET", "/api/channels/$accountId/playlists"))
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            Playlist(o.getString("id"), o.getString("title"))
        }
    }

    private fun parseVideo(o: JSONObject): Video {
        val tagsArr = o.optJSONArray("tags") ?: JSONArray()
        return Video(
            id = o.getInt("id"), status = o.getString("status"), title = o.str("title"),
            description = o.str("description") ?: "",
            tags = (0 until tagsArr.length()).map { tagsArr.getString(it) },
            categoryId = o.str("category_id"), madeForKids = o.optBoolean("made_for_kids"),
            synthetic = o.optBoolean("synthetic_media"), playlistId = o.str("playlist_id"),
            channelAccountId = o.int("channel_account_id"), channel = o.str("channel"),
            publishAt = o.str("publish_at")?.let { runCatching { java.time.OffsetDateTime.parse(it).toInstant() }.getOrNull() },
            postedAtLocal = o.str("posted_at_local"), fileName = o.str("file_name"),
            fileSize = if (o.isNull("file_size")) 0L else o.optLong("file_size"),
            youtubeUrl = o.str("youtube_url"), hasThumbnail = o.optBoolean("has_thumbnail"), error = o.str("error"),
        )
    }

    suspend fun videos(status: String = ""): List<Video> {
        val q = if (status.isBlank()) "" else "?status=" + URLEncoder.encode(status, "UTF-8")
        val arr = JSONArray(request("GET", "/api/videos$q"))
        return (0 until arr.length()).map { parseVideo(arr.getJSONObject(it)) }
    }

    suspend fun video(id: Int): Video = parseVideo(JSONObject(request("GET", "/api/videos/$id")))

    suspend fun update(id: Int, fields: JSONObject): Video =
        parseVideo(JSONObject(request("PATCH", "/api/videos/$id", fields)))

    suspend fun postNow(id: Int): Video = parseVideo(JSONObject(request("POST", "/api/videos/$id/post-now")))
    suspend fun retry(id: Int): Video = parseVideo(JSONObject(request("POST", "/api/videos/$id/retry")))
    suspend fun delete(id: Int) { request("DELETE", "/api/videos/$id") }

    suspend fun uploadThumbnail(id: Int, bytes: ByteArray, mime: String): Video {
        val boundary = "----ytsched" + System.currentTimeMillis()
        val out = ByteArrayOutputStream()
        out.write("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"thumb\"\r\nContent-Type: $mime\r\n\r\n".toByteArray())
        out.write(bytes)
        out.write("\r\n--$boundary--\r\n".toByteArray())
        val text = request("POST", "/api/videos/$id/thumbnail", raw = out.toByteArray(),
            contentType = "multipart/form-data; boundary=$boundary")
        return parseVideo(JSONObject(text))
    }

    fun thumbnailUrl(id: Int) = "$base/api/videos/$id/thumbnail"

    suspend fun connectLink(kind: String): String =
        JSONObject(request("POST", "/api/connect-link", JSONObject().put("kind", kind))).getString("url")

    suspend fun setStorage(accountId: Int) { request("POST", "/api/accounts/$accountId/storage") }
    suspend fun removeAccount(accountId: Int) { request("DELETE", "/api/accounts/$accountId") }

    companion object {
        private val iso = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")
        fun isoUtc(i: Instant): String = i.atOffset(ZoneOffset.UTC).format(iso)
    }
}
