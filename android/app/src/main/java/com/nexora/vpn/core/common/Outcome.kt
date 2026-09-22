package com.nexora.vpn.core.common

/**
 * The result of anything that talks to the network or the database.
 *
 * An explicit type rather than thrown exceptions, so a repository's failure
 * modes appear in its signature and a ViewModel cannot forget to handle one.
 * Every screen in this app branches on [Success] and [Failure]; nothing
 * catches a network exception by hand.
 */
sealed interface Outcome<out T> {

    data class Success<T>(val data: T) : Outcome<T>

    /**
     * Typed as `Outcome<Nothing>` so a failure can be returned from any
     * function whatever its success type — `is Outcome.Failure -> result`
     * compiles without a cast.
     */
    data class Failure(val error: AppError) : Outcome<Nothing>

    /**
     * The success value, or null on failure.
     *
     * A function rather than a property, and named to match `kotlin.Result`,
     * so call sites read the same whether they are unwrapping an [Outcome] or
     * the result of a `runCatching`.
     */
    fun getOrNull(): T? = (this as? Success)?.data

    fun errorOrNull(): AppError? = (this as? Failure)?.error

    val isSuccess: Boolean get() = this is Success
}

/**
 * Transforms the success value, leaving a failure untouched.
 *
 * A top-level extension rather than a member, because a member would be
 * invariant in `T` and could not return `Outcome<R>` from `Outcome<out T>`.
 */
inline fun <T, R> Outcome<T>.map(transform: (T) -> R): Outcome<R> = when (this) {
    is Outcome.Success -> Outcome.Success(transform(data))
    is Outcome.Failure -> this
}

inline fun <T> Outcome<T>.onSuccess(block: (T) -> Unit): Outcome<T> {
    if (this is Outcome.Success) block(data)
    return this
}

inline fun <T> Outcome<T>.onFailure(block: (AppError) -> Unit): Outcome<T> {
    if (this is Outcome.Failure) block(error)
    return this
}

/** The success value, or [fallback] when this is a failure. */
fun <T> Outcome<T>.getOrElse(fallback: T): T = when (this) {
    is Outcome.Success -> data
    is Outcome.Failure -> fallback
}
