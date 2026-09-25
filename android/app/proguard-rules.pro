# Retrofit + kotlinx.serialization keep rules.
-keepattributes Signature, InnerClasses, EnclosingMethod
-keepattributes RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations
-keepattributes AnnotationDefault

-keep,allowobfuscation,allowshrinking interface retrofit2.Call
-keep,allowobfuscation,allowshrinking class retrofit2.Response
-keep,allowobfuscation,allowshrinking class kotlin.coroutines.Continuation

# Serializable DTOs keep their generated serializers.
-if @kotlinx.serialization.Serializable class **
-keepclassmembers class <1> {
    static <1>$Companion Companion;
    static **$* *;
}
-keepclassmembers class **$* implements kotlinx.serialization.KSerializer {
    static <1>$* INSTANCE;
}

# Strip logging from release builds so nothing is written to logcat, where any
# app with READ_LOGS on a rooted device could read it.
-assumenosideeffects class android.util.Log {
    public static *** d(...);
    public static *** v(...);
    public static *** i(...);
}

# The Xray core is Go code reached through gomobile's JNI bridge; the native
# side looks these classes up by name.
-keep class go.** { *; }
-keep class libv2ray.** { *; }
