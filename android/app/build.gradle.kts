import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

// Signing config is read from keystore.properties, which is git-ignored.
// Without it the release build is simply unsigned rather than failing, so a
// fresh clone still builds.
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        load(keystorePropertiesFile.inputStream())
    }
}

// The API base URL is build configuration, not a secret. It lives in
// local.properties (git-ignored) so each developer can point at their own
// backend without editing a tracked file.
val localProperties = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) load(f.inputStream())
}

android {
    namespace = "com.nexora.vpn"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.nexora.vpn"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.0.1"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Public links for Help and About (a Telegram channel, the privacy
        // policy, the terms). Configuration, not secrets; empty means the
        // app does not show that entry. Quotes are dropped so a value cannot
        // break out of the generated Java string.
        fun link(key: String) = "\"" + localProperties.getProperty(key, "").replace("\"", "") + "\""
        buildConfigField("String", "SUPPORT_URL", link("support.url"))
        buildConfigField("String", "PRIVACY_URL", link("privacy.url"))
        buildConfigField("String", "TERMS_URL", link("terms.url"))

        // Persian and English only, so the APK does not carry resources for
        // every locale androidx ships.
        resourceConfigurations += setOf("en", "fa")
    }

    signingConfigs {
        if (keystorePropertiesFile.exists()) {
            create("release") {
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            isDebuggable = true
            buildConfigField(
                "String",
                "API_BASE_URL",
                "\"${localProperties.getProperty("api.base.url.debug") ?: "http://10.0.2.2:8000/"}\"",
            )
            // Body-level HTTP logging prints access tokens and config URIs,
            // so it is a debug-only capability. The release build also strips
            // android.util.Log entirely via ProGuard.
            buildConfigField("boolean", "LOG_NETWORK", "true")
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = signingConfigs.findByName("release")
            buildConfigField(
                "String",
                "API_BASE_URL",
                "\"${localProperties.getProperty("api.base.url") ?: "https://api.example.com/"}\"",
            )
            buildConfigField("boolean", "LOG_NETWORK", "false")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // Lets minSdk 24 use java.time and other newer APIs.
        isCoreLibraryDesugaringEnabled = true
    }

    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs += listOf("-opt-in=kotlin.RequiresOptIn")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    androidResources {
        // The core's AAR also ships a China-only GeoIP file this app never
        // routes by. Android's default ignore list, plus that file.
        ignoreAssetsPattern =
            "!.svn:!.git:!.ds_store:!*.scc:.*:<dir>_*:!CVS:!thumbs.db:!picasa.ini:!*~" +
            ":!geoip-only-cn-private.dat"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

// The Xray core (AndroidLibXrayLite, LGPL-3.0). Not committed: 60 MB of
// native code has no place in git history. scripts/fetch-xray-core.sh
// downloads the pinned release and checks its SHA-256; CI runs the same script.
val xrayCore = file("libs/libv2ray.aar")

val checkXrayCore by tasks.registering {
    doLast {
        if (!xrayCore.exists()) {
            throw GradleException(
                "Missing ${xrayCore.path}. Run: bash scripts/fetch-xray-core.sh " +
                    "(from the repository root). It downloads the pinned Xray core " +
                    "and verifies its checksum.",
            )
        }
    }
}
tasks.named("preBuild") { dependsOn(checkXrayCore) }

dependencies {
    implementation(files(xrayCore))

    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.3")

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    // MainActivity calls installSplashScreen(); the matching theme is
    // Theme.Nexora.Starting in res/values/themes.xml.
    implementation(libs.androidx.core.splashscreen)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.androidx.navigation.compose)

    implementation(libs.hilt.android)
    implementation(libs.hilt.navigation.compose)
    ksp(libs.hilt.compiler)

    implementation(libs.retrofit)
    implementation(libs.retrofit.serialization)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    // Not debugOnly: AppModule provides the interceptor unconditionally, and
    // its level is gated by BuildConfig.LOG_NETWORK. A debugImplementation
    // here would break the release build.
    implementation(libs.okhttp.logging)

    implementation(libs.room.runtime)
    implementation(libs.room.ktx)
    ksp(libs.room.compiler)

    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.work.runtime)

    implementation(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
    testImplementation(libs.mockk)
    testImplementation(libs.turbine)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
}
