package com.nmr.ytscheduler.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
            if (editable) OutlinedButton(onClick = { pickThumb.launch("image/*") }, enabled = !busy) {
                Text(if (v.hasThumbnail) "Change thumbnail" else "Add thumbnail")
            }
            if (!v.error.isNullOrBlank()) {
                Text(v.error.orEmpty(), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
            v.youtubeUrl?.let { url ->
                Button(onClick = { openUrl(ctx, url) }) { Text("Open on YouTube") }
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
