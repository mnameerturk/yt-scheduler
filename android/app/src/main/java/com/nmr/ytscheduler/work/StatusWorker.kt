package com.nmr.ytscheduler.work

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.nmr.ytscheduler.MainActivity
import com.nmr.ytscheduler.R
import com.nmr.ytscheduler.data.Api
import com.nmr.ytscheduler.data.Prefs
import java.util.concurrent.TimeUnit

/** Every ~15 minutes: asks the server what posted, failed or needs a thumbnail pick, and notifies about anything new. */
class StatusWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val prefs = Prefs(applicationContext)
        if (!prefs.configured) return Result.success()
        val videos = try {
            Api(applicationContext).videos("posted,failed")
        } catch (e: Exception) {
            return Result.success()
        }
        val now = videos.map { "${it.id}:${it.status}" }.toSet()
        if (prefs.seeded) {
            videos.filter { "${it.id}:${it.status}" !in prefs.seen }.forEach { v ->
                if (v.status == "posted") postNote(applicationContext, v.id, "Posted: ${v.displayTitle}", v.channel ?: "")
                else postNote(applicationContext, v.id, "Upload failed: ${v.displayTitle}", v.error ?: "Open the app to retry")
            }
        }
        prefs.seen = now
        prefs.seeded = true

        // thumbnails ready / Claude picked / Flow needs you
        runCatching {
            val events = Api(applicationContext).events(maxOf(prefs.lastEvent, 0))
            if (prefs.lastEvent >= 0) events.forEach { e ->
                val title = when (e.kind) {
                    "ready" -> "Pick a thumbnail"
                    "picked" -> "Thumbnail picked"
                    "meta" -> "Title & description ready"
                    else -> "Flow needs you"
                }
                postNote(applicationContext, 100000 + e.seq, title, e.text)
            }
            prefs.lastEvent = maxOf(prefs.lastEvent, events.maxOfOrNull { it.seq } ?: 0)
        }
        return Result.success()
    }

    companion object {
        private const val CHANNEL = "status"

        fun init(ctx: Context) {
            if (Build.VERSION.SDK_INT >= 26) {
                val nm = ctx.getSystemService(NotificationManager::class.java)
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL, "Upload status", NotificationManager.IMPORTANCE_DEFAULT)
                )
            }
            val req = PeriodicWorkRequestBuilder<StatusWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(ctx)
                .enqueueUniquePeriodicWork("status", ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun postNote(ctx: Context, id: Int, title: String, text: String) {
            if (Build.VERSION.SDK_INT >= 33 &&
                ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) return
            val open = PendingIntent.getActivity(
                ctx, 0, Intent(ctx, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            val n = NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setContentIntent(open)
                .setAutoCancel(true)
                .build()
            NotificationManagerCompat.from(ctx).notify(id, n)
        }
    }
}
