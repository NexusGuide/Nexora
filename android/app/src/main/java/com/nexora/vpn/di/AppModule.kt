package com.nexora.vpn.di

import com.nexora.vpn.BuildConfig
import com.nexora.vpn.core.network.AuthAuthenticator
import com.nexora.vpn.core.network.AuthInterceptor
import com.nexora.vpn.core.network.SessionEvents
import com.nexora.vpn.core.security.TokenStore
import com.nexora.vpn.data.remote.api.NexoraApi
import com.nexora.vpn.data.repository.AuthRepositoryImpl
import com.nexora.vpn.data.repository.ConfigRepositoryImpl
import com.nexora.vpn.data.repository.OrderRepositoryImpl
import com.nexora.vpn.data.repository.StoreRepositoryImpl
import com.nexora.vpn.data.repository.SubscriptionRepositoryImpl
import com.nexora.vpn.data.repository.UserRepositoryImpl
import com.nexora.vpn.domain.repository.AuthRepository
import com.nexora.vpn.domain.repository.ConfigRepository
import com.nexora.vpn.domain.repository.OrderRepository
import com.nexora.vpn.domain.repository.StoreRepository
import com.nexora.vpn.domain.repository.SubscriptionRepository
import com.nexora.vpn.domain.repository.UserRepository
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import java.util.concurrent.TimeUnit
import javax.inject.Provider
import javax.inject.Singleton
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides
    @Singleton
    fun json(): Json = Json {
        // An installed app must survive the server adding a field. Without
        // this, a backend deploy would crash every app in the wild.
        ignoreUnknownKeys = true
        isLenient = true
        explicitNulls = false
        coerceInputValues = true
    }

    @Provides
    @Singleton
    fun loggingInterceptor(): HttpLoggingInterceptor =
        HttpLoggingInterceptor().apply {
            // BODY logging prints access tokens and config URIs. It is allowed
            // in debug builds only, and the release build also strips
            // android.util.Log entirely via ProGuard.
            level = if (BuildConfig.LOG_NETWORK) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }

    @Provides
    @Singleton
    fun baseUrl(): String = BuildConfig.API_BASE_URL

    @Provides
    @Singleton
    fun sessionEvents(): SessionEvents = SessionEvents()

    @Provides
    @Singleton
    fun authenticator(
        tokenStore: TokenStore,
        clientProvider: Provider<OkHttpClient>,
        baseUrl: String,
        json: Json,
        sessionEvents: SessionEvents,
    ): AuthAuthenticator = AuthAuthenticator(
        tokenStore = tokenStore,
        clientProvider = clientProvider,
        baseUrl = baseUrl,
        json = json,
        onSessionLost = sessionEvents::notifySessionLost,
    )

    @Provides
    @Singleton
    fun okHttpClient(
        authInterceptor: AuthInterceptor,
        loggingInterceptor: HttpLoggingInterceptor,
        authenticator: AuthAuthenticator,
    ): OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(authInterceptor)
        .addInterceptor(loggingInterceptor)
        .authenticator(authenticator)
        // Generous but bounded. Mobile networks in Iran are often slow rather
        // than dead, and a 10s timeout would fail requests that would have
        // succeeded; no timeout at all would hang the UI indefinitely.
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    @Provides
    @Singleton
    fun retrofit(client: OkHttpClient, json: Json, baseUrl: String): Retrofit =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()

    @Provides
    @Singleton
    fun api(retrofit: Retrofit): NexoraApi = retrofit.create(NexoraApi::class.java)
}

@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds
    @Singleton
    abstract fun authRepository(impl: AuthRepositoryImpl): AuthRepository

    @Binds
    @Singleton
    abstract fun userRepository(impl: UserRepositoryImpl): UserRepository

    @Binds
    @Singleton
    abstract fun storeRepository(impl: StoreRepositoryImpl): StoreRepository

    @Binds
    @Singleton
    abstract fun orderRepository(impl: OrderRepositoryImpl): OrderRepository

    @Binds
    @Singleton
    abstract fun subscriptionRepository(
        impl: SubscriptionRepositoryImpl,
    ): SubscriptionRepository

    @Binds
    @Singleton
    abstract fun configRepository(impl: ConfigRepositoryImpl): ConfigRepository
}
