package com.nmr.ytscheduler.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import coil.compose.AsyncImage
import coil.request.ImageRequest
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.nmr.ytscheduler.data.Api
import com.nmr.ytscheduler.data.Channel
import com.nmr.ytscheduler.data.Playlist
import com.nmr.ytscheduler.data.Video
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalTime

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EditorScreen(videoId: Int, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val api = remember { Api(ctx) }
    val scope = rememberCoroutineScope()

    var video by remember { mutableStateOf<Video?>(null) }
    var channels by remember { mutableStateOf<List<Channel>>(emptyList()) }
    var playlists by remember { mutableStateOf<List<Playlist>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var thumbVersion by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var confirmDelete by remember { mutableStateOf(false) }

    var title by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    var tags by remember { mutableStateOf("") }
    var channelId by remember { mutableStateOf<Int?>(null) }
    var playlistId by remember { mutableStateOf<String?>(null) }
    var kids by remember { mutableStateOf(false) }
    var ai by remember { mutableStateOf(false) }
    var date by remember { mutableStateOf<LocalDate?>(null) }
    var time by remember { mutableStateOf(LocalTime.of(18, 0)) }

    fun fill(v: Video) {
        video = v
        title = v.title ?: ""
        description = v.description
        tags = v.tags.joinToString(", ")
        channelId = v.channelAccountId
        playlistId = v.playlistId
        kids = v.madeForKids
        ai = v.synthetic
        val z = v.publishAt?.atZone(zone())
        if (z != null) {
            date = z.toLocalDate()
            time = z.toLocalTime().withSecond(0).withNano(0)
        } else {
            date = null
        }
    }

    LaunchedEffect(videoId) {
        try {
            fill(api.video(videoId))
            channels = api.channels().filter { it.channelId != null }
        } catch (e: Exception) {
            loadError = e.message
        }
    }
    LaunchedEffect(channelId) {
        val id = channelId ?: return@LaunchedEffect
        playlists = runCatching { api.playlists(id) }.getOrDefault(emptyList())
    }

    fun act(block: suspend () -> Unit) {
        busy = true
        scope.launch {
            try {
                block()
            } catch (e: Exception) {
                toast(ctx, e.message ?: "Something went wrong")
            } finally {
                busy = false
            }
        }
    }

    fun fields(): JSONObject = JSONObject()
        .put("title", title.trim())
        .put("description", description)
        .put("tags", JSONArray(tags.split(",").map { it.trim() }.filter { it.isNotEmpty() }))
        .put("channel_account_id", channelId ?: JSONObject.NULL)
        .put("playlist_id", playlistId ?: JSONObject.NULL)
        .put("made_for_kids", kids)
        .put("synthetic_media", ai)
        .put("publish_at", date?.let { Api.isoUtc(toInstant(it, time)) } ?: JSONObject.NULL)

    val pickThumb = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        if (uri != null) act {
            val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                ?: throw IllegalStateException("Could not read the image")
            val mime = ctx.contentResolver.getType(uri) ?: "image/jpeg"
            fill(api.uploadThumbnail(videoId, bytes, mime))
            thumbVersion = System.currentTimeMillis()
            toast(ctx, "Thumbnail saved")
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Video") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                },
                actions = {
                    val st = video?.status
                    if (st != null && st != "posting") {
                        IconButton(onClick = { confirmDelete = true }) { Icon(Icons.Default.Delete, contentDescription = "Delete") }
                    }
                }
            )
        }
    ) { pad ->
        val v = video
        if (v == null) {
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.Center) {
                if (loadError != null) Text(loadError ?: "", color = MaterialTheme.colorScheme.error)
                else CircularProgressIndicator()
            }
            return@Scaffold
        }
        val editable = v.status in listOf("draft", "scheduled", "failed")
        Column(
            Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Thumb(api, v, 192, 108, thumbVersion)
                Spacer(Modifier.width(12.dp))
                Column {
                    StatusChip(v.status)
                    Text(v.fileName ?: "", style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (v.fileSize > 0) Text("${v.fileSize / 1024 / 1024} MB", style = MaterialTheme.typography.bodySmall)
                }
            }
            if (v.status != "posting") Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { pickThumb.launch("image/*") }, enabled = !busy) {
                    Text(if (v.hasThumbnail) "Own thumbnail" else "Add own thumbnail")
                }
                val making = v.thumbState in listOf("prompting", "queued", "generating")
                if (v.thumbOptions > 0) {
                    OutlinedButton(onClick = { act { fill(api.moreThumbnails(videoId)); toast(ctx, "Making 3 more…") } }, enabled = !busy && !making) {
                        Text("+ 3 more with Flow")
                    }
                } else {
                    OutlinedButton(onClick = { act { fill(api.autoThumbnail(videoId)) } }, enabled = !busy && !making) {
                        Text(if (v.thumbState == null) "Make 6 with Flow" else "Remake with Flow")
                    }
                }
            }
            FlowThumbs(api, v, busy) { n ->
                act {
                    fill(api.pickThumbnail(videoId, n))
                    thumbVersion = System.currentTimeMillis()
                    toast(ctx, if (v.status == "posted") "Thumbnail changed on YouTube" else "Thumbnail picked")
                }
            }
            if (!v.error.isNullOrBlank()) {
                Text(v.error.orEmpty(), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
            v.youtubeUrl?.let { url ->
                Button(onClick = { openUrl(ctx, url) }) { Text("Open on YouTube") }
            }

            TitleOptions(v, editable && !busy, onMore = { act { fill(api.moreTitles(videoId)); toast(ctx, "Writing 3 more titles…") } }) { t ->
                act { fill(api.update(videoId, JSONObject().put("title", t))); toast(ctx, "Title set") }
            }
            OutlinedTextField(
                value = title, onValueChange = { if (it.length <= 100) title = it }, enabled = editable,
                label = { Text("Title (${title.length}/100)") }, modifier = Modifier.fillMaxWidth()
            )
            OutlinedTextField(
                value = description, onValueChange = { description = it }, enabled = editable,
                label = { Text("Description") }, minLines = 4, modifier = Modifier.fillMaxWidth()
            )
            OutlinedTextField(
                value = tags, onValueChange = { tags = it }, enabled = editable,
                label = { Text("Tags (comma separated)") }, modifier = Modifier.fillMaxWidth()
            )

            Picker(
                label = "Channel: " + (channels.firstOrNull { it.id == channelId }?.title ?: v.channel ?: "choose"),
                enabled = editable,
                items = channels.map { (it.title ?: it.email ?: "?") to it.id },
                onPick = { channelId = it; playlistId = null },
            )
            Picker(
                label = "Playlist: " + (playlists.firstOrNull { it.id == playlistId }?.title ?: "none"),
                enabled = editable,
                items = listOf<Pair<String, String?>>("None" to null) + playlists.map { it.title to it.id },
                onPick = { playlistId = it },
            )

            Text("Post at", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = editable, onClick = {
                    pickDate(ctx, date ?: LocalDate.now()) { date = it }
                }) { Text(date?.let { formatDate(it) } ?: "Pick date") }
                OutlinedButton(enabled = editable, onClick = {
                    pickTime(ctx, time) { time = it; if (date == null) date = LocalDate.now() }
                }) { Text(formatTime(time)) }
            }
            if (date != null && editable) TextButton(onClick = { date = null }) { Text("Clear time (keep as draft)") }

            SwitchRow("Made for kids", kids, editable) { kids = it }
            SwitchRow("Altered or synthetic content (AI)", ai, editable) { ai = it }

            Spacer(Modifier.height(8.dp))
            if (busy) CircularProgressIndicator()
            if (editable) {
                Button(enabled = !busy, modifier = Modifier.fillMaxWidth(), onClick = {
                    if (channelId == null) { toast(ctx, "Choose a channel"); return@Button }
                    act { fill(api.update(videoId, fields())); toast(ctx, "Saved") }
                }) { Text(if (date != null) "Save schedule" else "Save as draft") }
                OutlinedButton(enabled = !busy, modifier = Modifier.fillMaxWidth(), onClick = {
                    act {
                        api.update(videoId, fields().put("publish_at", JSONObject.NULL))
                        fill(api.postNow(videoId))
                        toast(ctx, "Uploading to YouTube now")
                    }
                }) { Text("Post now") }
            }
            if (v.status == "failed") {
                OutlinedButton(enabled = !busy, modifier = Modifier.fillMaxWidth(), onClick = {
                    act { fill(api.retry(videoId)); toast(ctx, "Will retry shortly") }
                }) { Text("Retry") }
            }
        }
    }

    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete this video?") },
            text = { Text("It is removed from the queue and, if not posted yet, from Google Drive.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = false
                    act { api.delete(videoId); toast(ctx, "Deleted"); onBack() }
                }) { Text("Delete") }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } }
        )
    }
}

@Composable
private fun <T> Picker(label: String, enabled: Boolean, items: List<Pair<String, T>>, onPick: (T) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
            Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Icon(Icons.Default.ArrowDropDown, contentDescription = null)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            items.forEach { (name, value) ->
                DropdownMenuItem(text = { Text(name) }, onClick = { onPick(value); open = false })
            }
        }
    }
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}

/** The Flow options: tap one to use it. Shown while picking and after a pick (to switch). */
@Composable
private fun FlowThumbs(api: Api, v: Video, busy: Boolean, onPick: (Int) -> Unit) {
    val ctx = LocalContext.current
    val adding = v.thumbOptions > 0
    val note = when (v.thumbState) {
        "prompting" -> if (adding) "Claude is writing 3 more thumbnail prompts…" else "Claude is writing 6 thumbnail prompts…"
        "queued" -> "Waiting for Flow on the server…"
        "generating" -> if (adding) "Flow is making 3 more (a few minutes)…" else "Flow is making the thumbnails (about 5–10 min)…"
        "ready" -> "Tap the one you want." + (v.thumbDeadline?.let { " If you don't, Claude picks at $it." } ?: "")
        "picked" -> if (v.thumbBy == "Claude") "Claude picked option ${v.thumbPick}. Tap another to switch." else "Option ${v.thumbPick} in use. Tap another to switch."
        "failed" -> "Flow thumbnails failed. Tap Remake with Flow."
        else -> null
    } ?: return
    Text(note, style = MaterialTheme.typography.bodySmall)
    v.thumbNote?.takeIf { v.thumbState in listOf("failed", "generating", "picked") }?.let {
        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    if (v.thumbOptions <= 0) return
    val canPick = v.thumbState != "generating"
    (1..v.thumbOptions).chunked(2).forEach { pair ->
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            pair.forEach { n ->
                val chosen = v.thumbPick == n
                val req = ImageRequest.Builder(ctx)
                    .data(api.thumbOptionUrl(v.id, n) + "?v=${v.thumbVersion}_${v.thumbOptions}_${v.thumbNote.hashCode()}")
                    .apply { api.authHeaders.forEach { (k, value) -> addHeader(k, value) } }
                    .crossfade(true).build()
                Box(Modifier.weight(1f)) {
                    AsyncImage(
                        model = req, contentDescription = "Option $n", contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f).clip(RoundedCornerShape(8.dp))
                            .background(Color(0xFF2A2E38))
                            .border(if (chosen) 3.dp else 0.dp, if (chosen) MaterialTheme.colorScheme.primary else Color.Transparent, RoundedCornerShape(8.dp))
                            .clickable(enabled = !busy && !chosen && canPick) { onPick(n) }
                    )
                    Text(
                        "$n", color = Color.White, style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.align(Alignment.BottomEnd).padding(6.dp).background(Color(0x99000000), RoundedCornerShape(4.dp)).padding(horizontal = 6.dp, vertical = 2.dp)
                    )
                }
            }
            if (pair.size == 1) Spacer(Modifier.weight(1f))
        }
    }
}

/** Claude's 3 title options (from the script). Tap one to use it. */
@Composable
private fun TitleOptions(v: Video, enabled: Boolean, onMore: () -> Unit, onPick: (String) -> Unit) {
    val note = when (v.metaState) {
        "summarizing" -> "Haiku is reading the script…"
        "writing" -> "Opus is writing titles, description and thumbnail prompts…"
        "more" -> "Opus is writing 3 more titles…"
        "failed" -> v.metaNote ?: "Claude could not write the title"
        else -> null
    }
    note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
    if (v.titleOptions.isEmpty()) return
    Text("Title options", style = MaterialTheme.typography.labelLarge)
    v.titleOptions.forEachIndexed { i, t ->
        val current = t == v.title
        OutlinedButton(onClick = { onPick(t) }, enabled = enabled && !current, modifier = Modifier.fillMaxWidth()) {
            Text((if (current) "✓ " else "${i + 1}. ") + t, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth())
        }
    }
    TextButton(onClick = onMore, enabled = enabled && v.metaState !in listOf("summarizing", "writing", "more")) {
        Text("+ 3 more titles")
    }
}
