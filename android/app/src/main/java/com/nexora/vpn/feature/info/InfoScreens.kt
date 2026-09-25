@file:OptIn(ExperimentalMaterial3Api::class)

package com.nexora.vpn.feature.info

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.BugReport
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Gavel
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.PrivacyTip
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.nexora.vpn.BuildConfig
import com.nexora.vpn.R
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.SectionTitle
import com.nexora.vpn.core.ui.SettingsRow
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.SubScreen

private fun Context.openUrl(url: String) {
    runCatching {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/** Questions customers actually ask, answered for this app. */
private val FAQ = listOf(
    R.string.faq_q_slow to R.string.faq_a_slow,
    R.string.faq_q_iran to R.string.faq_a_iran,
    R.string.faq_q_devices to R.string.faq_a_devices,
    R.string.faq_q_pay to R.string.faq_a_pay,
    R.string.faq_q_logs to R.string.faq_a_logs,
)

@Composable
fun HelpScreen(onBack: () -> Unit, onOpenLogs: () -> Unit) {
    val context = LocalContext.current
    SubScreen(title = stringResource(R.string.help_title), onBack = onBack) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
        ) {
            SectionTitle(stringResource(R.string.help_faq))
            FAQ.forEach { (q, a) -> FaqItem(stringResource(q), stringResource(a)) }

            SectionTitle(stringResource(R.string.help_contact))
            if (BuildConfig.SUPPORT_URL.isNotBlank()) {
                SettingsRow(
                    icon = Icons.AutoMirrored.Filled.Send,
                    title = stringResource(R.string.help_support_channel),
                    subtitle = BuildConfig.SUPPORT_URL,
                    onClick = { context.openUrl(BuildConfig.SUPPORT_URL) },
                )
            }
            // A problem report is the connection log, which the customer sees
            // in full and sends themselves — nothing is uploaded silently.
            SettingsRow(
                icon = Icons.Filled.BugReport,
                title = stringResource(R.string.help_report),
                subtitle = stringResource(R.string.help_report_body),
                onClick = onOpenLogs,
            )
            Gap(Spacing.lg)
        }
    }
}

@Composable
private fun FaqItem(question: String, answer: String) {
    var open by rememberSaveable(question) { mutableStateOf(false) }
    NexoraCard(Modifier.fillMaxWidth().padding(bottom = Spacing.sm), onClick = { open = !open }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(question, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            Icon(if (open) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore, contentDescription = null)
        }
        if (open) {
            Text(
                answer,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = Spacing.sm),
            )
        }
    }
}

@Composable
fun AboutScreen(onBack: () -> Unit, onOpenLicenses: () -> Unit, coreVersion: String) {
    val context = LocalContext.current
    SubScreen(title = stringResource(R.string.about_title), onBack = onBack) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Gap(Spacing.lg)
            Icon(
                painterResource(R.drawable.ic_launcher_monochrome),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(96.dp),
            )
            Text(stringResource(R.string.app_name), style = MaterialTheme.typography.headlineMedium)
            Text(
                stringResource(R.string.about_version, BuildConfig.VERSION_NAME),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Gap()
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
                SettingsRow(
                    icon = Icons.Filled.Memory,
                    title = stringResource(R.string.about_core),
                    subtitle = coreVersion,
                )
                if (BuildConfig.PRIVACY_URL.isNotBlank()) {
                    SettingsRow(
                        icon = Icons.Filled.PrivacyTip,
                        title = stringResource(R.string.about_privacy),
                        onClick = { context.openUrl(BuildConfig.PRIVACY_URL) },
                    )
                }
                if (BuildConfig.TERMS_URL.isNotBlank()) {
                    SettingsRow(
                        icon = Icons.Filled.Gavel,
                        title = stringResource(R.string.about_terms),
                        onClick = { context.openUrl(BuildConfig.TERMS_URL) },
                    )
                }
                SettingsRow(
                    icon = Icons.Filled.Code,
                    title = stringResource(R.string.about_licenses),
                    onClick = onOpenLicenses,
                )
            }
        }
    }
}

/** The license texts shipped in assets/licenses, readable in the app. */
@Composable
fun LicensesScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val files = remember {
        runCatching { context.assets.list("licenses")?.sorted().orEmpty() }.getOrDefault(emptyList())
    }
    var open by rememberSaveable { mutableStateOf<String?>(null) }

    SubScreen(title = stringResource(R.string.about_licenses), onBack = onBack) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
        ) {
            Text(
                stringResource(R.string.licenses_body),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = Spacing.sm),
            )
            files.forEach { name ->
                SettingsRow(
                    icon = Icons.Filled.Description,
                    title = name.removeSuffix(".txt").replace('-', ' '),
                    onClick = { open = if (open == name) null else name },
                )
                if (open == name) {
                    val text = remember(name) {
                        runCatching {
                            context.assets.open("licenses/$name").bufferedReader().use { it.readText() }
                        }.getOrDefault("")
                    }
                    Text(
                        text,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.fillMaxWidth().padding(vertical = Spacing.sm).clickable { open = null },
                    )
                }
            }
            Gap(Spacing.lg)
        }
    }
}
