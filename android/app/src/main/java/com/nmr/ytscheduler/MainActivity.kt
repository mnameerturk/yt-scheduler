package com.nmr.ytscheduler

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.nmr.ytscheduler.data.Prefs
import com.nmr.ytscheduler.ui.AppTheme
import com.nmr.ytscheduler.ui.EditorScreen
import com.nmr.ytscheduler.ui.QueueScreen
import com.nmr.ytscheduler.ui.SettingsScreen
import com.nmr.ytscheduler.work.StatusWorker

sealed class Screen {
    data object Queue : Screen()
    data class Editor(val id: Int) : Screen()
    data class Settings(val firstRun: Boolean) : Screen()
}

class MainActivity : ComponentActivity() {
    private var screen by mutableStateOf<Screen>(Screen.Queue)
    private var refreshKey by mutableIntStateOf(0)

    private val notifPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        StatusWorker.init(this)
        if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        if (!Prefs(this).configured) screen = Screen.Settings(firstRun = true)

        setContent {
            AppTheme {
                val current = screen
                BackHandler(enabled = current is Screen.Editor || (current is Screen.Settings && !current.firstRun)) {
                    screen = Screen.Queue
                    refreshKey++
                }
                when (current) {
                    Screen.Queue -> QueueScreen(
                        refreshKey = refreshKey,
                        onOpen = { screen = Screen.Editor(it) },
                        onSettings = { screen = Screen.Settings(firstRun = false) },
                    )
                    is Screen.Editor -> EditorScreen(current.id) { screen = Screen.Queue; refreshKey++ }
                    is Screen.Settings -> SettingsScreen(current.firstRun) { screen = Screen.Queue; refreshKey++ }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refreshKey++
    }
}
