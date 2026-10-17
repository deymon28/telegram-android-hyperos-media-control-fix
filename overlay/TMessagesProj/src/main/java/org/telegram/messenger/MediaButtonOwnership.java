package org.telegram.messenger;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.AudioPlaybackConfiguration;
import android.media.SoundPool;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import java.util.List;

/**
 * Keeps a paused Telegram track eligible for headset media buttons.
 *
 * Android sends media keys to the most recent app that still has an active audio
 * player. A SoundPool counts as active from its first play() until release(), even
 * when it is silent. Another app holding such a pool, such as a second Telegram client
 * after an in-chat sound, therefore takes the buttons as soon as this app pauses.
 * While our paused track is the latest media playback, this class holds an equivalent
 * silent marker. It drops the marker as soon as another media player starts or our
 * player is dismissed, so other media apps keep their normal priority.
 */
final class MediaButtonOwnership {
    private static final long OTHER_PLAYBACK_CONFIRM_MS = 700;

    private final AudioManager audioManager;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private AudioManager.AudioPlaybackCallback callback;
    private SoundPool marker;
    private boolean playing;
    private final Runnable confirmOtherPlayback = this::releaseIfOtherPlaybackContinues;

    MediaButtonOwnership(Context context) {
        audioManager = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
    }

    void onPlaybackStateChanged(boolean isPlaying) {
        playing = isPlaying;
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || audioManager == null || !isPlaying || marker != null) {
            return;
        }
        try {
            marker = new SoundPool.Builder()
                    .setMaxStreams(1)
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build())
                    .build();
            // No sound is loaded, so nothing is played; the call only reports the player as started.
            marker.play(0, 0f, 0f, 0, 0, 1f);
            if (callback == null) {
                callback = new AudioManager.AudioPlaybackCallback() {
                    @Override
                    public void onPlaybackConfigChanged(List<AudioPlaybackConfiguration> configs) {
                        if (!playing && hasMediaPlayback(configs)) {
                            // Our own track can still be reported active for a moment after pause.
                            handler.removeCallbacks(confirmOtherPlayback);
                            handler.postDelayed(confirmOtherPlayback, OTHER_PLAYBACK_CONFIRM_MS);
                        }
                    }
                };
                audioManager.registerAudioPlaybackCallback(callback, handler);
            }
        } catch (RuntimeException e) {
            FileLog.e(e);
            releaseMarker();
        }
    }

    void release() {
        handler.removeCallbacks(confirmOtherPlayback);
        releaseMarker();
        if (callback != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            audioManager.unregisterAudioPlaybackCallback(callback);
            callback = null;
        }
    }

    // Public callbacks list only active players. Our own track is paused here, so
    // active media playback belongs to another app or to an in-app video.
    private static boolean hasMediaPlayback(List<AudioPlaybackConfiguration> configs) {
        for (AudioPlaybackConfiguration config : configs) {
            int usage = config.getAudioAttributes().getUsage();
            if (usage == AudioAttributes.USAGE_MEDIA || usage == AudioAttributes.USAGE_GAME
                    || usage == AudioAttributes.USAGE_UNKNOWN) {
                return true;
            }
        }
        return false;
    }

    private void releaseIfOtherPlaybackContinues() {
        if (!playing && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && hasMediaPlayback(audioManager.getActivePlaybackConfigurations())) {
            releaseMarker();
        }
    }

    private void releaseMarker() {
        if (marker != null) {
            marker.release();
            marker = null;
        }
    }
}
