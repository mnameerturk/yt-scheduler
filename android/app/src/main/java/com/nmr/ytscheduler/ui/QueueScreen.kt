package com.nmr.ytscheduler.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.nmr.ytscheduler.data.Api
import com.nmr.ytscheduler.data.Video
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val tabs = listOf(
    "Upcoming" to "draft,scheduled,posting",
    "Posted" to "posted",
    "Failed" to "failed",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QueueScreen(refreshKey: Int, onOpen: (Int) -> Unit, onSettings: () -> Unit) {
    val ctx = LocalContext.current
    val api = remember { Api(ctx) }
    val scope = rememberCoroutineScope()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var videos by remember { mutableStateOf<List<Video>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var testMode by remember { mutableStateOf(false) }

    suspend fun load() {
        loading = true
        try {
            videos = api.videos(tabs[tab].second)
            error = null
        } catch (e: Exception) {
            error = e.message
        } finally {
            loading = false
        }
    }

    LaunchedEffect(tab, refreshKey) {
        runCatching { testMode = api.health().optBoolean("test_mode") }
        while (true) {
            load()
            delay(20_000)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("NMR Scheduler") },
                actions = {
                    IconButton(onClick = { scope.launch { load() } }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                    IconButton(onClick = onSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Channels and settings")
                    }
                }
            )
        }
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            TabRow(selectedTabIndex = tab) {
                tabs.forEachIndexed { i, t ->
                    Tab(selected = tab == i, onClick = { tab = i }, text = { Text(t.first) })
                }
            }
            if (testMode) {
                Text(
                    "Test mode: uploads stay private until the YouTube audit is approved.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFFFFD54F),
                    modifier = Modifier.fillMaxWidth().background(Color(0xFF4A3500)).padding(10.dp)
                )
            }
            error?.let {
                Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
            }
            if (loading && videos.isEmpty()) {
                Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else if (videos.isEmpty() && error == null) {
                Text(
                    when (tab) {
                        0 -> "Nothing queued. Videos sent from the PC show up here."
                        1 -> "No posted videos yet."
                        else -> "No failed uploads."
                    },
                    modifier = Modifier.padding(24.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            LazyColumn(
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(videos, key = { it.id }) { v -> VideoCard(v, api) { onOpen(v.id) } }
            }
        }
    }
}

@Composable
fun Thumb(api: Api, v: Video, width: Int, height: Int, version: Long = 0L) {
    val ctx = LocalContext.current
    val mod = Modifier.size(width.dp, height.dp).clip(RoundedCornerShape(8.dp)).background(Color(0xFF2A2E38))
    if (v.hasThumbnail) {
        val req = ImageRequest.Builder(ctx)
            .data(api.thumbnailUrl(v.id) + "?v=${if (version > 0) version else v.thumbVersion}")
            .apply { api.authHeaders.forEach { (k, value) -> addHeader(k, value) } }
            .crossfade(true)
            .build()
        AsyncImage(model = req, contentDescription = null, contentScale = ContentScale.Crop, modifier = mod)
    } else {
        Box(mod, contentAlignment = Alignment.Center) {
            Text("No thumbnail", color = Color(0xFF9AA0AA), style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun VideoCard(v: Video, api: Api, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Thumb(api, v, 128, 72)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(v.displayTitle, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(
                    v.channel ?: "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                val time = if (v.status == "posted") "Posted " + (v.postedAtLocal ?: "")
                else v.publishAt?.let { formatInstant(it) } ?: "No time set"
                Text(time, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(4.dp))
                StatusChip(v.status)
                thumbBadge(v)?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                }
                if (!v.error.isNullOrBlank() && v.status != "posted") {
                    Text(
                        v.error.orEmpty(), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error, maxLines = 2, overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }
}

fun thumbBadge(v: Video): String? = when (v.thumbState) {
    "prompting", "queued" -> "Thumbnails: waiting for Flow"
    "generating" -> "Thumbnails: Flow is making them"
    "ready" -> "Pick a thumbnail" + (v.thumbDeadline?.let { " (Claude picks at $it)" } ?: "")
    "failed" -> "Thumbnails failed"
    else -> null
}
