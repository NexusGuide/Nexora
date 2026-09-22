package com.nexora.vpn.core.ui

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat

/**
 * Nexora's colour and type system.
 *
 * Both light and dark are defined explicitly rather than derived, because a
 * VPN app is opened at night more than most and a washed-out auto-dark palette
 * is the first thing that looks cheap.
 */

private val Teal = Color(0xFF00BFA5)
private val TealDark = Color(0xFF00897B)
private val TealLight = Color(0xFF5DF2D6)
private val Navy = Color(0xFF0B1E2D)
private val NavySoft = Color(0xFF13293D)
private val Slate = Color(0xFF1E3A4C)

// Connection states. Green/amber/red are conventional enough that colour alone
// is a hint, never the only signal — every status also carries a label.
val ConnectedGreen = Color(0xFF16A34A)
val ConnectingAmber = Color(0xFFF59E0B)
val DisconnectedGrey = Color(0xFF64748B)
val DangerRed = Color(0xFFDC2626)

private val LightColors = lightColorScheme(
    primary = TealDark,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFB2DFDB),
    onPrimaryContainer = Color(0xFF00251F),
    secondary = Slate,
    onSecondary = Color.White,
    background = Color(0xFFF8FAFC),
    onBackground = Color(0xFF0F172A),
    surface = Color.White,
    onSurface = Color(0xFF0F172A),
    surfaceVariant = Color(0xFFE2E8F0),
    onSurfaceVariant = Color(0xFF475569),
    error = DangerRed,
    onError = Color.White,
    outline = Color(0xFFCBD5E1),
)

private val DarkColors = darkColorScheme(
    primary = Teal,
    onPrimary = Color(0xFF00201A),
    primaryContainer = TealDark,
    onPrimaryContainer = TealLight,
    secondary = Color(0xFF7DD3FC),
    onSecondary = Navy,
    background = Navy,
    onBackground = Color(0xFFE2E8F0),
    surface = NavySoft,
    onSurface = Color(0xFFE2E8F0),
    surfaceVariant = Slate,
    onSurfaceVariant = Color(0xFF94A3B8),
    error = Color(0xFFF87171),
    onError = Color(0xFF450A0A),
    outline = Color(0xFF334155),
)

/**
 * Type scale. Deliberately not using a custom font file: Persian rendering
 * depends heavily on the font, and the system font handles Persian correctly
 * on every device. A bundled Latin-first font would break Persian shaping,
 * which is a worse outcome than looking slightly less distinctive.
 */
private val NexoraTypography = Typography(
    displaySmall = TextStyle(fontSize = 34.sp, fontWeight = FontWeight.Bold),
    headlineMedium = TextStyle(fontSize = 26.sp, fontWeight = FontWeight.SemiBold),
    headlineSmall = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.Medium),
)

object Spacing {
    val xs = 4.dp
    val sm = 8.dp
    val md = 16.dp
    val lg = 24.dp
    val xl = 32.dp
}

@Composable
fun NexoraTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    /**
     * Material You is off by default. The brand colour is part of how a VPN
     * app signals trust, and letting the wallpaper recolour it is not worth
     * the novelty here.
     */
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit,
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context)
            else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColors
        else -> LightColors
    }

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            WindowCompat.getInsetsController(window, view)
                .isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = NexoraTypography,
        content = content,
    )
}
