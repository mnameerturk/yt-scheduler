package com.nmr.ytscheduler.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.nmr.ytscheduler.data.Api
import com.nmr.ytscheduler.data.Channel
import com.nmr.ytscheduler.data.Prefs
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(firstRun: Boolean, onDone: () -> Unit) {
    val ctx = LocalContext.current
    val prefs = remember { Prefs(ctx) }
    val api = remember { Api(ctx) }
    val scope = rememberCoroutineScope()

    var server by remember { mutableStateOf(prefs.server.ifBlank { "https://dayroom-cinch-scorer.ngrok-free.dev" }) }
    var password by remember { mutableStateOf(prefs.password) }
    var status by remember { mutableStateOf<String?>(null) }
    var channels by remember { mutableStateOf<List<Channel>>(emptyList()) }
    var styleFor by remember { mutableStateOf<Channel?>(null) }
    var styleText by remember { mutableStateOf("") }
    var footerText by remember { mutableStateOf("") }
    var reload by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf(false) }

    LaunchedEffect(reload) {
        if (!prefs.configured) return@LaunchedEffect
        try {
            channels = api.channels()
            status = null
        } catch (e: Exception) {
            status = e.message
        }
    }

    fun act(block: suspend () -> Unit) {
        busy = true
        scope.launch {
            try { block() } catch (e: Exception) { toast(ctx, e.message ?: "Something went wrong") } finally { busy = false }
        }
    }

    styleFor?.let { c ->
        AlertDialog(
            onDismissRequest = { styleFor = null },
            title = { Text("Style · ${c.title ?: ""}") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "Claude follows this when it writes the Flow prompts for this channel. " +
                            "Example: photorealistic cinematic, one huge subject, black background, 2-3 bold yellow words.",
                        style = MaterialTheme.typography.bodySmall
                    )
                    OutlinedTextField(value = styleText, onValueChange = { styleText = it.take(1500) },
                        label = { Text("Thumbnail notes") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(value = footerText, onValueChange = { footerText = it.take(3000) },
                        label = { Text("Added to every description") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val id = c.id
                    act { api.setThumbStyle(id, styleText.trim(), footerText.trim()); toast(ctx, "Saved") }
                    styleFor = null
                }) { Text("Save") }
            },
            dismissButton = { TextButton(onClick = { styleFor = null }) { Text("Cancel") } }
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (firstRun) "Connect to your server" else "Channels & settings") },
                navigationIcon = {
                    if (!firstRun) IconButton(onClick = onDone) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Text("Server", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = server, onValueChange = { server = it }, singleLine = true,
                label = { Text("Server address") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth()
            )
            OutlinedTextField(
                value = password, onValueChange = { password = it }, singleLine = true,
                label = { Text("App password") }, visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth()
            )
            Button(enabled = !busy, onClick = {
                prefs.server = server
                prefs.password = password
                act {
                    api.channels()
                    toast(ctx, "Connected")
                    reload++
                    if (firstRun) onDone()
                }
            }) { Text("Save and test") }
            status?.let { Text(it, color = MaterialTheme.colorScheme.error) }

            if (prefs.configured) {
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                Text("YouTube channels and Drive", style = MaterialTheme.typography.titleMedium)
                if (channels.none { it.isStorage && it.hasDrive }) {
                    Text(
                        "Connect Google Drive so the PC has somewhere to put videos.",
                        color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall
                    )
                }
                channels.forEach { c -> AccountCard(c, busy, onStorage = {
                    act { api.setStorage(c.id); reload++ }
                }, onRemove = {
                    act { api.removeAccount(c.id); reload++ }
                }, onStyle = {
                    act { val (st, ft) = api.thumbStyle(c.id); styleText = st; footerText = ft; styleFor = c }
                }) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = !busy, onClick = { act { openUrl(ctx, api.connectLink("youtube")) } }) {
                        Text("+ YouTube channel")
                    }
                    OutlinedButton(enabled = !busy, onClick = { act { openUrl(ctx, api.connectLink("drive")) } }) {
                        Text("+ Google Drive")
                    }
                }
                Text(
                    "Sign-in opens in your browser. When it says connected, come back and tap Refresh.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                TextButton(onClick = { reload++ }) { Text("Refresh list") }

                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                Text("Use with Claude", style = MaterialTheme.typography.titleMedium)
                Text(
                    "In Claude: Settings → Connectors → Add custom connector, name it NMR Scheduler and paste this link. " +
                        "Keep the link private: it gives full control of the scheduler.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                val clip = androidx.compose.ui.platform.LocalClipboardManager.current
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = !busy, onClick = {
                        act { clip.setText(androidx.compose.ui.text.AnnotatedString(api.mcpUrl())); toast(ctx, "Link copied") }
                    }) { Text("Copy Claude link") }
                    TextButton(enabled = !busy, onClick = {
                        act { clip.setText(androidx.compose.ui.text.AnnotatedString(api.resetMcpUrl())); toast(ctx, "New link copied; the old one stops working") }
                    }) { Text("New link") }
                }
                TextButton(onClick = { openUrl(ctx, prefs.server) }) { Text("Open web dashboard") }
            }
        }
    }
}

@Composable
private fun AccountCard(c: Channel, busy: Boolean, onStorage: () -> Unit, onRemove: () -> Unit, onStyle: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text(c.title ?: "Drive only", fontWeight = FontWeight.SemiBold)
            Text(c.email ?: "", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            val badges = buildList {
                if (c.channelId != null) add("YouTube")
                if (c.hasDrive) add(if (c.isStorage) "Drive storage" else "Drive")
                if (c.needsReconnect) add("Reconnect needed")
            }
            Text(badges.joinToString(" · "), style = MaterialTheme.typography.bodySmall)
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (c.hasDrive && !c.isStorage) TextButton(enabled = !busy, onClick = onStorage) { Text("Use this Drive") }
                if (c.channelId != null) TextButton(enabled = !busy, onClick = onStyle) { Text("Style & description") }
                TextButton(enabled = !busy, onClick = onRemove) { Text("Remove") }
            }
        }
    }
}
