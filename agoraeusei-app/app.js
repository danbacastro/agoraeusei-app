import React, { useRef, useState, useEffect, useCallback } from "react";
import { View, Text, SafeAreaView, Platform, BackHandler, RefreshControl, Linking } from "react-native";
import { WebView } from "react-native-webview";
import NetInfo from "@react-native-community/netinfo";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";

SplashScreen.preventAutoHideAsync();

// 🔗 sua URL do Streamlit
const APP_URL = "https://agoraeusei.streamlit.app/";

export default function App() {
  const webRef = useRef(null);
  const [progress, setProgress] = useState(0);
  const [isOnline, setIsOnline] = useState(true);
  const [canGoBack, setCanGoBack] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const sub = NetInfo.addEventListener(state => {
      setIsOnline(Boolean(state.isConnected && state.isInternetReachable));
    });
    const timer = setTimeout(() => SplashScreen.hideAsync(), 800);
    return () => { sub && sub(); clearTimeout(timer); };
  }, []);

  // Botão "voltar" no Android navega dentro da WebView
  useEffect(() => {
    const onBack = () => {
      if (canGoBack && webRef.current) {
        webRef.current.goBack();
        return true;
      }
      return false;
    };
    const bh = BackHandler.addEventListener("hardwareBackPress", onBack);
    return () => bh.remove();
  }, [canGoBack]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    webRef.current?.reload();
    setTimeout(() => setRefreshing(false), 700);
  }, []);

  const OfflineBanner = () => (
    <View style={{ backgroundColor: "#fee2e2", paddingVertical: 8 }}>
      <Text style={{ textAlign: "center", color: "#991b1b" }}>
        Sem conexão. Algumas funções podem não funcionar.
      </Text>
    </View>
  );

  const LoadingBar = () => (
    progress > 0 && progress < 1 ? (
      <View style={{ height: 2, backgroundColor: "#e5e7eb" }}>
        <View style={{ height: 2, width: `${progress * 100}%`, backgroundColor: "#2563eb" }} />
      </View>
    ) : null
  );

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#ffffff" }}>
      <StatusBar style={Platform.OS === "ios" ? "dark" : "auto"} />
      {!isOnline && <OfflineBanner />}
      <LoadingBar />
      <WebView
        ref={webRef}
        source={{ uri: APP_URL }}
        originWhitelist={["*"]}
        setSupportMultipleWindows={false}
        startInLoadingState
        javaScriptEnabled
        domStorageEnabled
        sharedCookiesEnabled          // iOS: mantém cookies (login) na WebView
        thirdPartyCookiesEnabled      // Android: idem
        allowsBackForwardNavigationGestures
        onLoadProgress={({ nativeEvent }) => setProgress(nativeEvent.progress)}
        onNavigationStateChange={(s) => setCanGoBack(s.canGoBack)}
        pullToRefreshEnabled={Platform.OS === "android"}
        refreshControl={Platform.OS === "ios" ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} /> : undefined}
        // Abrir links externos no navegador do sistema
        onShouldStartLoadWithRequest={(req) => {
          const isSameOrigin = req.url.startsWith(APP_URL);
          if (!isSameOrigin) {
            Linking.openURL(req.url);
            return false;
          }
          return true;
        }}
      />
    </SafeAreaView>
  );
}
