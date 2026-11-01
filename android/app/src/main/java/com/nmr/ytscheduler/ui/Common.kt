package com.nmr.ytscheduler.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.text.format.DateFormat
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val YtRed = Color(0xFFE53935)

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme())
        darkColorScheme(primary = Color(0xFFFF6B6B), secondary = Color(0xFFFF8A80))
    else
        lightColorScheme(primary = YtRed, secondary = Color(0xFFB71C1C))
    MaterialTheme(colorScheme = scheme, content = content)
}

fun toast(ctx: Context, msg: String) = Toast.makeText(ctx, msg, Toast.LENGTH_LONG).show()

fun openUrl(ctx: Context, url: String) {
    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}

private val dateTimeFmt = DateTimeFormatter.ofPattern("EEE d MMM, h:mm a")
private val dateFmt = DateTimeFormatter.ofPattern("EEE d MMM yyyy")
private val timeFmt = DateTimeFormatter.ofPattern("h:mm a")

fun zone(): ZoneId = ZoneId.systemDefault()
fun formatInstant(i: Instant): String = i.atZone(zone()).format(dateTimeFmt)
fun formatDate(d: LocalDate): String = d.format(dateFmt)
fun formatTime(t: LocalTime): String = t.format(timeFmt)
fun toInstant(d: LocalDate, t: LocalTime): Instant = LocalDateTime.of(d, t).atZone(zone()).toInstant()

fun pickDate(ctx: Context, initial: LocalDate, onPick: (LocalDate) -> Unit) {
    val dlg = DatePickerDialog(
        ctx, { _, y, m, d -> onPick(LocalDate.of(y, m + 1, d)) },
        initial.year, initial.monthValue - 1, initial.dayOfMonth
    )
    dlg.datePicker.minDate = System.currentTimeMillis() - 1000
    dlg.show()
}

fun pickTime(ctx: Context, initial: LocalTime, onPick: (LocalTime) -> Unit) {
    TimePickerDialog(
        ctx, { _, h, min -> onPick(LocalTime.of(h, min)) },
        initial.hour, initial.minute, DateFormat.is24HourFormat(ctx)
    ).show()
}

fun statusLabel(s: String) = when (s) {
    "draft" -> "No time set"
    "scheduled" -> "Scheduled"
    "posting" -> "Uploading"
    "posted" -> "Posted"
    "failed" -> "Failed"
    else -> s
}

@Composable
fun StatusChip(status: String) {
    val bg = when (status) {
        "scheduled" -> Color(0xFF1E3A5F)
        "posting" -> Color(0xFF6D4C00)
        "posted" -> Color(0xFF1B5E20)
        "failed" -> Color(0xFF7F1D1D)
        else -> Color(0xFF444444)
    }
    Box(
        Modifier
            .background(bg, RoundedCornerShape(10.dp))
            .padding(horizontal = 8.dp, vertical = 2.dp)
    ) {
        Text(statusLabel(status), color = Color.White, fontSize = 12.sp)
    }
}
