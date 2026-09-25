package com.nexora.vpn.core.ui

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import com.nexora.vpn.core.settings.ThemeMode

/**
 * Nexora's colour and type system: deep navy surfaces with a bright accent,
 * as in the product design. Dark is the default — a VPN app is opened at
 * night more than most — with Light, System and AMOLED (true black, which
 * saves power on OLED screens) as choices.
 */

/** The accent colours offered in Appearance. The first is the brand teal. */
object Accents {
    data class Accent(val dark: Color, val light: Color)

    val all = listOf(
        Accent(dark = Color(0xFF2DD4BF), light = Color(0xFF0F9F8F)), // teal
        Accent(dark = Color(0xFF22D3EE), light = Color(0xFF0891B2)), // cyan
        Accent(dark = Color(0xFF60A5FA), light = Color(0xFF2563EB)), // blue
        Accent(dark = Color(0xFFA78BFA), light = Color(0xFF7C3AED)), // violet
        Accent(dark = Color(0xFFFB923C), light = Color(0xFFEA580C)), // orange
        Accent(dark = Color(0xFFE879F9), light = Color(0xFFC026D3)), // fuchsia
    )

    fun at(index: Int): Accent = all.getOrElse(index) { all.first() }
}

// Connection states. Colour is a hint, never the only signal — every status
// also carries a label.
val ConnectedGreen = Color(0xFF22C55E)
val ConnectingAmber = Color(0xFFF59E0B)
val DisconnectedGrey = Color(0xFF64748B)
val DangerRed = Color(0xFFEF4444)

private val Navy = Color(0xFF0A111C)
private val NavySurface = Color(0xFF111B2A)
private val NavyRaised = Color(0xFF172436)
private val NavyOutline = Color(0xFF24344B)

private fun darkScheme(accent: Color, amoled: Boolean) = darkColorScheme(
    primary = accent,
    onPrimary = Color(0xFF04201C),
    primaryContainer = accent.copy(alpha = 0.18f),
    onPrimaryContainer = accent,
    secondary = Color(0xFF7DD3FC),
    onSecondary = Navy,
    background = if (amoled) Color.Black else Navy,
    onBackground = Color(0xFFE6EDF6),
    surface = if (amoled) Color(0xFF0A0A0A) else NavySurface,
    onSurface = Color(0xFFE6EDF6),
    surfaceVariant = if (amoled) Color(0xFF141414) else NavyRaised,
    onSurfaceVariant = Color(0xFF93A4BA),
    surfaceContainer = if (amoled) Color(0xFF0E0E0E) else NavySurface,
    surfaceContainerHigh = if (amoled) Color(0xFF161616) else NavyRaised,
    error = Color(0xFFF87171),
    onError = Color(0xFF450A0A),
    outline = if (amoled) Color(0xFF262626) else NavyOutline,
    outlineVariant = if (amoled) Color(0xFF1C1C1C) else Color(0xFF1B2A3E),
)

private fun lightScheme(accent: Color) = lightColorScheme(
    primary = accent,
    onPrimary = Color.White,
    primaryContainer = accent.copy(alpha = 0.14f),
    onPrimaryContainer = accent,
    secondary = Color(0xFF334155),
    onSecondary = Color.White,
    background = Color(0xFFF6F8FB),
    onBackground = Color(0xFF0F172A),
    surface = Color.White,
    onSurface = Color(0xFF0F172A),
    surfaceVariant = Color(0xFFEEF2F7),
    onSurfaceVariant = Color(0xFF52627A),
    error = DangerRed,
    onError = Color.White,
    outline = Color(0xFFD5DDE8),
    outlineVariant = Color(0xFFE6EBF2),
)

/**
 * Type scale. No bundled font: Persian shaping depends on the font, and the
 * system font handles it correctly on every device.
 */
private val NexoraTypography = Typography(
    displayLarge = TextStyle(fontSize = 44.sp, fontWeight = FontWeight.Bold),
    displaySmall = TextStyle(fontSize = 34.sp, fontWeight = FontWeight.Bold),
    headlineMedium = TextStyle(fontSize = 26.sp, fontWeight = FontWeight.SemiBold),
    headlineSmall = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.Medium),
    titleSmall = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 12.sp, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Medium),
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
    mode: ThemeMode = ThemeMode.DARK,
    accentIndex: Int = 0,
    content: @Composable () -> Unit,
) {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK, ThemeMode.AMOLED -> true
    }
    val accent = Accents.at(accentIndex)
    val colorScheme = if (dark) {
        darkScheme(accent.dark, amoled = mode == ThemeMode.AMOLED)
    } else {
        lightScheme(accent.light)
    }

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !dark
                isAppearanceLightNavigationBars = !dark
            }
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = NexoraTypography,
        content = content,
    )
}
