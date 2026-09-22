package com.nexora.vpn.core.network

import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.data.remote.dto.ApiEnvelope
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull
import retrofit2.Response

/**
 * Turns a Retrofit call into an [Outcome], classifying every failure once.
 *
 * Without this, each repository would need its own try/catch, and the
 * classification would drift — one screen treating a timeout as fatal while
 * another retries it.
 */
object ApiCall {

    private val lenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

    suspend operator fun <T : Any> invoke(
        block: suspend () -> Response<ApiEnvelope<T>>,
    ): Outcome<T> = withContext(Dispatchers.IO) {
        try {
            val response = block()
            val envelope = response.body()

            if (response.isSuccessful && envelope?.success == true) {
                val data = envelope.data
                if (data != null) {
                    Outcome.Success(data)
                } else {
                    // 200 with no payload: the endpoint returned an
                    // acknowledgement. Callers that need data will have
                    // declared a non-Unit type, so this is a contract breach.
                    Outcome.Failure(AppError.Unknown("Empty response body"))
                }
            } else {
                Outcome.Failure(classify(response, envelope))
            }
        } catch (e: CancellationException) {
            // Never swallowed: the screen went away and the coroutine must die.
            throw e
        } catch (e: UnknownHostException) {
            Outcome.Failure(AppError.Network("No connection"))
        } catch (e: SocketTimeoutException) {
            Outcome.Failure(AppError.Timeout("The server took too long"))
        } catch (e: SSLException) {
            // Worth distinguishing: in Iran this often means interception
            // rather than a broken server, and "check your connection" is the
            // wrong advice.
            Outcome.Failure(AppError.Network("Secure connection failed"))
        } catch (e: IOException) {
            Outcome.Failure(AppError.Network(e.message))
        } catch (e: Exception) {
            Outcome.Failure(AppError.Unknown(e.javaClass.simpleName))
        }
    }

    private fun <T> classify(
        response: Response<ApiEnvelope<T>>,
        envelope: ApiEnvelope<T>?,
    ): AppError {
        // The error envelope is also sent on a non-2xx, but Retrofit puts it in
        // errorBody rather than body.
        val error = envelope?.error ?: parseErrorBody(response)
        val requestId = envelope?.requestId

        return when (response.code()) {
            401 -> AppError.Unauthorized(
                code = error?.code ?: "UNAUTHORIZED",
                message = error?.message,
            )

            403, 409, 422, 400, 404, 429, 501, 503 -> AppError.Rejected(
                code = error?.code ?: "REQUEST_REJECTED",
                message = error?.message,
                details = error?.details?.let(::flatten) ?: emptyMap(),
            )

            in 500..599 -> AppError.Server(error?.message, requestId)

            else -> AppError.Unknown(error?.message ?: "HTTP ${response.code()}")
        }
    }

    private fun <T> parseErrorBody(
        response: Response<ApiEnvelope<T>>,
    ): com.nexora.vpn.data.remote.dto.ApiError? = try {
        response.errorBody()?.string()?.takeIf { it.isNotBlank() }?.let { raw ->
            lenientJson.decodeFromString(
                ApiEnvelope.serializer(JsonElement.serializer()),
                raw,
            ).error
        }
    } catch (e: Exception) {
        // A body that is not our envelope (a proxy's HTML error page, say).
        null
    }

    /**
     * Flattens the JSON `details` object into plain Kotlin values the UI can
     * read without touching kotlinx.serialization types.
     */
    private fun flatten(details: Map<String, JsonElement>): Map<String, Any?> =
        details.mapValues { (_, value) -> unwrap(value) }

    private fun unwrap(element: JsonElement): Any? = when (element) {
        is JsonNull -> null
        is JsonPrimitive -> when {
            element.isString -> element.content
            element.booleanOrNull != null -> element.booleanOrNull
            element.longOrNull != null -> element.longOrNull
            else -> element.content
        }
        is JsonObject -> element.mapValues { (_, v) -> unwrap(v) }
        is kotlinx.serialization.json.JsonArray -> element.map(::unwrap)
    }
}
