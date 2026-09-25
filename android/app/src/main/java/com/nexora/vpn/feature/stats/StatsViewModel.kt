package com.nexora.vpn.feature.stats

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.stats.ConnectionLog
import com.nexora.vpn.core.stats.DailyUsage
import com.nexora.vpn.core.stats.SessionRecord
import com.nexora.vpn.core.stats.UsageStore
import com.nexora.vpn.core.vpn.VpnController
import com.nexora.vpn.feature.servers.ServerCatalog
import com.nexora.vpn.domain.model.Subscription
import dagger.hilt.android.lifecycle.HiltViewModel
import java.time.LocalDate
import javax.inject.Inject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class StatsPeriod(val days: Int) { TODAY(1), WEEK(7), MONTH(30) }

data class ServerUsage(val serverName: String, val bytes: Long, val sessions: Int)

data class StatsUiState(
    val period: StatsPeriod = StatsPeriod.WEEK,
    /** One entry per day of the period, oldest first, zero-filled. */
    val days: List<DailyUsage> = emptyList(),
    val totalDown: Long = 0,
    val totalUp: Long = 0,
    val topServers: List<ServerUsage> = emptyList(),
    val subscription: Subscription? = null,
    val sessions: List<SessionRecord> = emptyList(),
)

/**
 * Statistics as measured on this phone (see UsageStore). The subscription's
 * own used/limit, from the panel, is shown alongside — the two can differ,
 * because the panel also counts other devices and the phone only counts
 * itself.
 */
@HiltViewModel
class StatsViewModel @Inject constructor(
    private val usage: UsageStore,
    private val catalog: ServerCatalog,
    private val vpn: VpnController,
) : ViewModel() {

    private val _state = MutableStateFlow(StatsUiState())
    val state: StateFlow<StatsUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            // Refreshed while the screen is open, so today's numbers move
            // during a session.
            while (isActive) {
                reload()
                delay(10_000)
            }
        }
    }

    fun setPeriod(period: StatsPeriod) {
        _state.update { it.copy(period = period) }
        viewModelScope.launch { reload() }
    }

    private suspend fun reload() {
        val period = _state.value.period
        val (daily, sessions) = withContext(Dispatchers.IO) { usage.daily() to usage.sessions() }
        val today = LocalDate.now()
        val byDay = daily.associateBy { it.day }
        val days = (period.days - 1 downTo 0).map { back ->
            val day = today.minusDays(back.toLong())
            byDay[day] ?: DailyUsage(day, 0, 0)
        }
        val since = today.minusDays((period.days - 1).toLong())
        val inPeriod = sessions.filter {
            java.time.Instant.ofEpochMilli(it.startedAtMs)
                .atZone(java.time.ZoneId.systemDefault()).toLocalDate() >= since
        }
        val top = inPeriod.groupBy { it.serverName }
            .map { (name, list) -> ServerUsage(name, list.sumOf { it.totalBytes }, list.size) }
            .sortedByDescending { it.bytes }
            .take(5)
        _state.update {
            it.copy(
                days = days,
                totalDown = days.sumOf { d -> d.bytesDown },
                totalUp = days.sumOf { d -> d.bytesUp },
                topServers = top,
                subscription = catalog.state.value.subscription,
                sessions = sessions,
            )
        }
    }

    fun clearHistory() {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { usage.clear() }
            reload()
        }
    }

    val log: ConnectionLog get() = vpn.log
}
