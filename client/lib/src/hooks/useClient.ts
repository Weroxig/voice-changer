import { useEffect, useMemo, useRef, useState } from "react";
import { VoiceChangerClient } from "../VoiceChangerClient";
import { useClientSetting } from "./useClientSetting";
import { IndexedDBStateAndMethod, useIndexedDB } from "./useIndexedDB";
import { ServerSettingState, useServerSetting } from "./useServerSetting";
import { useWorkletNodeSetting } from "./useWorkletNodeSetting";
import { useWorkletSetting } from "./useWorkletSetting";
import { ClientSetting, DefaultClientSettng, VoiceChangerClientSetting, WorkletNodeSetting, WorkletSetting } from "../const";

export type UseClientProps = {
    audioContext: AudioContext | null;
};

export type ClientState = {
    initialized: boolean;
    setting: ClientSetting;
    // 各種設定I/Fへの参照
    setVoiceChangerClientSetting: (_voiceChangerClientSetting: VoiceChangerClientSetting) => void;
    setServerUrl: (url: string) => void;
    start: () => Promise<void>;
    stop: () => Promise<void>;
    reloadClientSetting: () => Promise<void>;

    setWorkletNodeSetting: (_workletNodeSetting: WorkletNodeSetting) => void;
    startOutputRecording: () => void;
    stopOutputRecording: () => Promise<Float32Array>;
    trancateBuffer: () => Promise<void>;

    setWorkletSetting: (_workletSetting: WorkletSetting) => void;
    // workletSetting: WorkletSetting
    // workletSetting: WorkletSettingState
    // clientSetting: ClientSettingState
    // workletNodeSetting: WorkletNodeSettingState
    serverSetting: ServerSettingState;
    indexedDBState: IndexedDBStateAndMethod;

    // モニタリングデータ
    bufferingTime: number;
    performance: PerformanceStats;
    // setClientType: (val: ClientType) => void

    // 情報取得
    getInfo: () => Promise<void>;
    // 設定クリア
    clearSetting: () => Promise<void>;
    // AudioOutputElement  設定
    setAudioOutputElementId: (elemId: string) => void;
    setAudioMonitorElementId: (elemId: string) => void;

    errorMessage: string;
    resetErrorMessage: () => void;
};

export type PerformanceStats = {
    vol: number;
    responseTime: number;
    preprocessTime: number;
    mainprocessTime: number;
    postprocessTime: number;
};

export const useClient = (props: UseClientProps): ClientState => {
    const [initialized, setInitialized] = useState<boolean>(false);
    const [setting, setSetting] = useState<ClientSetting>(DefaultClientSettng);
    // (1-1) クライアント
    const voiceChangerClientRef = useRef<VoiceChangerClient | null>(null);
    const [voiceChangerClient, setVoiceChangerClient] = useState<VoiceChangerClient | null>(voiceChangerClientRef.current);
    //// クライアント初期化待ち用フラグ
    const initializedResolveRef = useRef<(value: void | PromiseLike<void>) => void>();
    const initializedPromise = useMemo(() => {
        return new Promise<void>((resolve) => {
            initializedResolveRef.current = resolve;
        });
    }, []);

    // (1-2) 各種設定I/F
    const voiceChangerClientSetting = useClientSetting({ voiceChangerClient, voiceChangerClientSetting: setting.voiceChangerClientSetting });
    const workletNodeSetting = useWorkletNodeSetting({ voiceChangerClient: voiceChangerClient, workletNodeSetting: setting.workletNodeSetting });
    useWorkletSetting({ voiceChangerClient, workletSetting: setting.workletSetting });
    const serverSetting = useServerSetting({ voiceChangerClient });
    const indexedDBState = useIndexedDB({ clientType: null });

    // (1-3) モニタリングデータ
    const [bufferingTime, setBufferingTime] = useState<number>(0);
    const [performance, setPerformance] = useState<PerformanceStats>({
        vol: 0,
        responseTime: 0,
        preprocessTime: 0,
        mainprocessTime: 0,
        postprocessTime: 0,
    });
    const [errorMessage, setErrorMessage] = useState<string>("");

    const resetErrorMessage = () => {
        setErrorMessage("");
    };

    // 設定データ管理
    const { setItem, getItem, removeItem } = useIndexedDB({ clientType: null });
    // 設定データの更新と保存
    const _setSetting = (_setting: ClientSetting) => {
        const storeData = { ..._setting };
        storeData.voiceChangerClientSetting = { ...storeData.voiceChangerClientSetting };
        if (typeof storeData.voiceChangerClientSetting.audioInput != "string") {
            storeData.voiceChangerClientSetting.audioInput = "none";
        }
        setItem("clientSetting", storeData);

        setSetting(_setting);
    };
    // 設定データ初期化
    useEffect(() => {
        if (!voiceChangerClient) {
            return;
        }
        const loadSettings = async () => {
            const server = await serverSetting.reloadServerInfo();
            const _setting = (await getItem("clientSetting")) as ClientSetting;
            if (_setting) {
                // Modify cached settings with the server's setting
                _setting.workletNodeSetting.inputChunkNum = server.serverReadChunkSize
                setSetting(_setting);
            } else {
                // Modify default settings with the server's setting
                setSetting({ ...setting, workletNodeSetting: { ...setting.workletNodeSetting, inputChunkNum: server.serverReadChunkSize } });
            }
        };
        loadSettings();
    }, [voiceChangerClient]);

    // (2-1) クライアント初期化処理
    useEffect(() => {
        const initialized = async () => {
            if (!props.audioContext) {
                return;
            }
            const voiceChangerClient = new VoiceChangerClient(props.audioContext, true, {
                notifySendBufferingTime: (val: number) => {
                    setBufferingTime(val);
                },
                notifyPerformanceStats: (responseTime: number, vol: number, perf: number[]) => {
                    const [preprocessTime, mainprocessTime, postprocessTime] = perf;
                    setPerformance({
                        vol,
                        responseTime,
                        preprocessTime: Math.ceil(preprocessTime * 1000),
                        mainprocessTime: Math.ceil(mainprocessTime * 1000),
                        postprocessTime: Math.ceil(postprocessTime * 1000),
                    });
                },
                notifyException: (_: string, mes: string) => {
                    // TODO: Refactor
                    // const serverError = `Error code: ${code}\n\n${mes}`
                    // console.error(serverError);
                    setErrorMessage(mes);
                }
            });

            await voiceChangerClient.isInitialized();
            voiceChangerClientRef.current = voiceChangerClient;
            setVoiceChangerClient(voiceChangerClientRef.current);
            console.log("[useClient] client initialized");

            // const audio = document.getElementById(props.audioOutputElementId) as HTMLAudioElement
            // audio.srcObject = voiceChangerClientRef.current.stream
            // audio.play()
            initializedResolveRef.current!();
            setInitialized(true);
        };
        initialized();
    }, [props.audioContext]);

    const setAudioOutputElementId = (elemId: string) => {
        if (!voiceChangerClientRef.current) {
            console.warn("[voiceChangerClient] is not ready for set audio output.");
            return;
        }
        const audio = document.getElementById(elemId) as HTMLAudioElement;
        if (audio.paused) {
            audio.srcObject = voiceChangerClientRef.current.stream;
            audio.play();
        }
    };

    const setAudioMonitorElementId = (elemId: string) => {
        if (!voiceChangerClientRef.current) {
            console.warn("[voiceChangerClient] is not ready for set audio output.");
            return;
        }
        const audio = document.getElementById(elemId) as HTMLAudioElement;
        if (audio.paused) {
            audio.srcObject = voiceChangerClientRef.current.monitorStream;
            audio.play();
        }
    };

    // (2-2) 情報リロード
    const getInfo = useMemo(() => {
        return async () => {
            await initializedPromise;
            await voiceChangerClientSetting.reloadClientSetting(); // 実質的な処理の意味はない
            // await serverSetting.reloadServerInfo();
        };
    }, [voiceChangerClientSetting.reloadClientSetting]);

    const clearSetting = async () => {
        await removeItem("clientSetting");
    };

    // 設定変更
    const setVoiceChangerClientSetting = (_voiceChangerClientSetting: VoiceChangerClientSetting) => {
        setting.voiceChangerClientSetting = _voiceChangerClientSetting;
        console.log("setting.voiceChangerClientSetting", setting.voiceChangerClientSetting);
        // workletSettingIF.setSetting(_workletSetting)
        _setSetting({ ...setting });
    };

    const setWorkletNodeSetting = (_workletNodeSetting: WorkletNodeSetting) => {
        setting.workletNodeSetting = _workletNodeSetting;
        console.log("setting.workletNodeSetting", setting.workletNodeSetting);
        // workletSettingIF.setSetting(_workletSetting)
        _setSetting({ ...setting });
    };

    const setWorkletSetting = (_workletSetting: WorkletSetting) => {
        setting.workletSetting = _workletSetting;
        console.log("setting.workletSetting", setting.workletSetting);
        // workletSettingIF.setSetting(_workletSetting)
        _setSetting({ ...setting });
    };

    return {
        initialized,
        setting,
        // 各種設定I/Fへの参照
        setVoiceChangerClientSetting,
        setServerUrl: voiceChangerClientSetting.setServerUrl,
        start: voiceChangerClientSetting.start,
        stop: voiceChangerClientSetting.stop,
        reloadClientSetting: voiceChangerClientSetting.reloadClientSetting,

        setWorkletNodeSetting,
        startOutputRecording: workletNodeSetting.startOutputRecording,
        stopOutputRecording: workletNodeSetting.stopOutputRecording,
        trancateBuffer: workletNodeSetting.trancateBuffer,

        setWorkletSetting,
        // workletSetting: workletSettingIF.setting,
        serverSetting,
        indexedDBState,

        // モニタリングデータ
        bufferingTime,
        performance,

        // 情報取得
        getInfo,

        // 設定クリア
        clearSetting,

        // AudioOutputElement  設定
        setAudioOutputElementId,
        setAudioMonitorElementId,

        errorMessage,
        resetErrorMessage,
    };
};
