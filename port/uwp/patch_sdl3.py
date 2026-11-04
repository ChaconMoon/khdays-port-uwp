#!/usr/bin/env python3
"""
patch_sdl3.py - Enables SDL_GPU with Direct3D 12 on UWP (WindowsStore / Xbox).
Applies targeted patches to SDL3-uwp so that:
1. SDL_GPU_D3D12 is compiled under WINDOWS_STORE (CMakeLists.txt).
2. D3D12_PrepareDriver & D3D12_CreateDevice link directly against D3D12/DXGI symbols
   instead of trying to LoadPackagedLibrary them at runtime.
3. D3D12_INTERNAL_CreateSwapchain uses IDXGIFactory2::CreateSwapChainForCoreWindow
   with the window's ICoreWindow instead of HWND-based CreateSwapChainForHwnd.
"""

import os
import sys

def patch_file(path, old, new):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if new in content:
        print(f"Already patched: {path}")
        return
    if old not in content:
        raise ValueError(f"Target snippet not found in {path}:\n{old[:120]}...")
    content = content.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Successfully patched {path}")

def main(sdl_dir):
    cmakelists = os.path.join(sdl_dir, "CMakeLists.txt")
    patch_file(
        cmakelists,
        "if(SDL_GPU AND NOT WINDOWS_STORE)\n  if(HAVE_D3D11_H)",
        "if(SDL_GPU)\n  if(HAVE_D3D11_H AND NOT WINDOWS_STORE)"
    )

    d3d12_c = os.path.join(sdl_dir, "src", "gpu", "d3d12", "SDL_gpu_d3d12.c")

    # 1. ResizeBuffers: avoid invalid flags on CoreWindow (and avoid #ifdef inside C macro call)
    patch_file(
        d3d12_c,
        """    // Resize the swapchain
    HRESULT res = IDXGISwapChain_ResizeBuffers(
        windowData->swapchain,
        0, // Keep buffer count the same
        0, // use client window width
        0, // use client window height
        DXGI_FORMAT_UNKNOWN, // Keep the old format
        renderer->supportsTearing ? DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING : 0);""",
        """    Uint32 resizeFlags = 0;
#ifndef SDL_PLATFORM_WINRT
    if (renderer->supportsTearing) {
        resizeFlags = DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING;
    }
#endif

    // Resize the swapchain
    HRESULT res = IDXGISwapChain_ResizeBuffers(
        windowData->swapchain,
        0, // Keep buffer count the same
        0, // use client window width
        0, // use client window height
        DXGI_FORMAT_UNKNOWN, // Keep the old format
        resizeFlags);"""
    )

    # 2. CreateSwapchain: use IDXGIFactory2_CreateSwapChainForCoreWindow on WinRT
    patch_file(
        d3d12_c,
        """    if (renderer->supportsTearing) {
        swapchainDesc.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING;
    } else {
        swapchainDesc.Flags = 0;
    }

#ifndef SDL_PLATFORM_WINRT
    if (!IsWindow(dxgiHandle)) {
        return false;
    }
#endif

    // Create the swapchain!
    res = IDXGIFactory4_CreateSwapChainForHwnd(
        renderer->factory,
        (IUnknown *)renderer->commandQueue,
        dxgiHandle,
        &swapchainDesc,
        &fullscreenDesc,
        NULL,
        &swapchain);
    CHECK_D3D12_ERROR_AND_RETURN("Could not create swapchain", false);""",
        """#ifdef SDL_PLATFORM_WINRT
    swapchainDesc.Flags = 0;
    IUnknown *coreWindow = (IUnknown *)SDL_GetPointerProperty(SDL_GetWindowProperties(windowData->window), SDL_PROP_WINDOW_WINRT_WINDOW_POINTER, NULL);
    if (!coreWindow) {
        return false;
    }
    res = IDXGIFactory2_CreateSwapChainForCoreWindow(
        (IDXGIFactory2 *)renderer->factory,
        (IUnknown *)renderer->commandQueue,
        coreWindow,
        &swapchainDesc,
        NULL,
        &swapchain);
    CHECK_D3D12_ERROR_AND_RETURN("Could not create swapchain for CoreWindow", false);
#else
    if (renderer->supportsTearing) {
        swapchainDesc.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING;
    } else {
        swapchainDesc.Flags = 0;
    }

    if (!IsWindow(dxgiHandle)) {
        return false;
    }

    // Create the swapchain!
    res = IDXGIFactory4_CreateSwapChainForHwnd(
        renderer->factory,
        (IUnknown *)renderer->commandQueue,
        dxgiHandle,
        &swapchainDesc,
        &fullscreenDesc,
        NULL,
        &swapchain);
    CHECK_D3D12_ERROR_AND_RETURN("Could not create swapchain", false);
#endif"""
    )

    # 3. MakeWindowAssociation: skip Win32 HWND association on WinRT
    patch_file(
        d3d12_c,
        """    res = IDXGISwapChain3_GetParent(
        swapchain3,
        D3D_GUID(D3D_IID_IDXGIFactory1),
        (void **)&pParent);
    if (FAILED(res)) {
        SDL_LogWarn(
            SDL_LOG_CATEGORY_GPU,
            "Could not get swapchain parent! Error Code: " HRESULT_FMT,
            res);
    } else {
        // Disable DXGI window crap
        res = IDXGIFactory1_MakeWindowAssociation(
            pParent,
            dxgiHandle,
            DXGI_MWA_NO_WINDOW_CHANGES);
        if (FAILED(res)) {
            SDL_LogWarn(
                SDL_LOG_CATEGORY_GPU,
                "MakeWindowAssociation failed! Error Code: " HRESULT_FMT,
                res);
        }

        // We're done with the parent now
        IDXGIFactory1_Release(pParent);
    }""",
        """#ifndef SDL_PLATFORM_WINRT
    res = IDXGISwapChain3_GetParent(
        swapchain3,
        D3D_GUID(D3D_IID_IDXGIFactory1),
        (void **)&pParent);
    if (FAILED(res)) {
        SDL_LogWarn(
            SDL_LOG_CATEGORY_GPU,
            "Could not get swapchain parent! Error Code: " HRESULT_FMT,
            res);
    } else {
        // Disable DXGI window crap
        res = IDXGIFactory1_MakeWindowAssociation(
            pParent,
            dxgiHandle,
            DXGI_MWA_NO_WINDOW_CHANGES);
        if (FAILED(res)) {
            SDL_LogWarn(
                SDL_LOG_CATEGORY_GPU,
                "MakeWindowAssociation failed! Error Code: " HRESULT_FMT,
                res);
        }

        // We're done with the parent now
        IDXGIFactory1_Release(pParent);
    }
#endif"""
    )

    # 4. D3D12_PrepareDriver: static function binding on WinRT
    patch_file(
        d3d12_c,
        """    // Can we load D3D12?

    d3d12Dll = SDL_LoadObject(D3D12_DLL);
    if (d3d12Dll == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find " D3D12_DLL);
        return false;
    }

    pD3D12CreateDevice = (PFN_D3D12_CREATE_DEVICE)SDL_LoadFunction(
        d3d12Dll,
        D3D12_CREATE_DEVICE_FUNC);
    if (pD3D12CreateDevice == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find function " D3D12_CREATE_DEVICE_FUNC " in " D3D12_DLL);
        SDL_UnloadObject(d3d12Dll);
        return false;
    }

    // Can we load DXGI?

    dxgiDll = SDL_LoadObject(DXGI_DLL);
    if (dxgiDll == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find " DXGI_DLL);
        return false;
    }

    pCreateDXGIFactory1 = (pfnCreateDXGIFactory1)SDL_LoadFunction(
        dxgiDll,
        CREATE_DXGI_FACTORY1_FUNC);
    if (pCreateDXGIFactory1 == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find function " CREATE_DXGI_FACTORY1_FUNC " in " DXGI_DLL);
        SDL_UnloadObject(dxgiDll);
        return false;
    }""",
        """#if defined(SDL_PLATFORM_WINRT)
    pD3D12CreateDevice = D3D12CreateDevice;
    pCreateDXGIFactory1 = (pfnCreateDXGIFactory1)CreateDXGIFactory1;
#else
    // Can we load D3D12?

    d3d12Dll = SDL_LoadObject(D3D12_DLL);
    if (d3d12Dll == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find " D3D12_DLL);
        return false;
    }

    pD3D12CreateDevice = (PFN_D3D12_CREATE_DEVICE)SDL_LoadFunction(
        d3d12Dll,
        D3D12_CREATE_DEVICE_FUNC);
    if (pD3D12CreateDevice == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find function " D3D12_CREATE_DEVICE_FUNC " in " D3D12_DLL);
        SDL_UnloadObject(d3d12Dll);
        return false;
    }

    // Can we load DXGI?

    dxgiDll = SDL_LoadObject(DXGI_DLL);
    if (dxgiDll == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find " DXGI_DLL);
        return false;
    }

    pCreateDXGIFactory1 = (pfnCreateDXGIFactory1)SDL_LoadFunction(
        dxgiDll,
        CREATE_DXGI_FACTORY1_FUNC);
    if (pCreateDXGIFactory1 == NULL) {
        SDL_LogWarn(SDL_LOG_CATEGORY_GPU, "D3D12: Could not find function " CREATE_DXGI_FACTORY1_FUNC " in " DXGI_DLL);
        SDL_UnloadObject(dxgiDll);
        return false;
    }
#endif"""
    )

    # 5. D3D12_PrepareDriver cleanup: skip unloading DLLs on WinRT
    patch_file(
        d3d12_c,
        """    IDXGIAdapter1_Release(adapter);
    IDXGIFactory1_Release(factory);

    SDL_UnloadObject(d3d12Dll);
    SDL_UnloadObject(dxgiDll);""",
        """    IDXGIAdapter1_Release(adapter);
    IDXGIFactory1_Release(factory);

#if !defined(SDL_PLATFORM_WINRT)
    SDL_UnloadObject(d3d12Dll);
    SDL_UnloadObject(dxgiDll);
#endif"""
    )

    # 6. D3D12_CreateDevice dxgi load: bypass SDL_LoadObject(DXGI_DLL) on WinRT
    patch_file(
        d3d12_c,
        """#if !(defined(SDL_PLATFORM_XBOXONE) || defined(SDL_PLATFORM_XBOXSERIES))
    // Load the DXGI library
    renderer->dxgi_dll = SDL_LoadObject(DXGI_DLL);""",
        """#if !(defined(SDL_PLATFORM_XBOXONE) || defined(SDL_PLATFORM_XBOXSERIES) || defined(SDL_PLATFORM_WINRT))
    // Load the DXGI library
    renderer->dxgi_dll = SDL_LoadObject(DXGI_DLL);"""
    )

    # 7. D3D12_CreateDevice CreateDXGIFactory1: direct assignment on WinRT
    patch_file(
        d3d12_c,
        """    // Load the CreateDXGIFactory1 function
    pCreateDXGIFactory1 = (pfnCreateDXGIFactory1)SDL_LoadFunction(
        renderer->dxgi_dll,
        CREATE_DXGI_FACTORY1_FUNC);
    if (pCreateDXGIFactory1 == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: " CREATE_DXGI_FACTORY1_FUNC, NULL);
    }""",
        """#if defined(SDL_PLATFORM_WINRT)
    pCreateDXGIFactory1 = (pfnCreateDXGIFactory1)CreateDXGIFactory1;
#else
    // Load the CreateDXGIFactory1 function
    pCreateDXGIFactory1 = (pfnCreateDXGIFactory1)SDL_LoadFunction(
        renderer->dxgi_dll,
        CREATE_DXGI_FACTORY1_FUNC);
    if (pCreateDXGIFactory1 == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: " CREATE_DXGI_FACTORY1_FUNC, NULL);
    }
#endif"""
    )

    # 8. D3D12_CreateDevice D3D12CreateDevice & SerializeRootSignature: direct assignment on WinRT
    patch_file(
        d3d12_c,
        """    // Load the D3D library
    renderer->d3d12_dll = SDL_LoadObject(D3D12_DLL);
    if (renderer->d3d12_dll == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not find " D3D12_DLL, NULL);
    }

    // Load the CreateDevice function
#if (defined(SDL_PLATFORM_XBOXONE) || defined(SDL_PLATFORM_XBOXSERIES))
    D3D12XboxCreateDeviceFunc = (PFN_D3D12_XBOX_CREATE_DEVICE)SDL_LoadFunction(
        renderer->d3d12_dll,
        "D3D12XboxCreateDevice");
    if (D3D12XboxCreateDeviceFunc == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: D3D12XboxCreateDevice", NULL);
    }
#else
    pD3D12CreateDevice = (PFN_D3D12_CREATE_DEVICE)SDL_LoadFunction(
        renderer->d3d12_dll,
        D3D12_CREATE_DEVICE_FUNC);
    if (pD3D12CreateDevice == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: " D3D12_CREATE_DEVICE_FUNC, NULL);
    }
#endif

    renderer->pD3D12SerializeRootSignature = (PFN_D3D12_SERIALIZE_ROOT_SIGNATURE)SDL_LoadFunction(
        renderer->d3d12_dll,
        D3D12_SERIALIZE_ROOT_SIGNATURE_FUNC);
    if (renderer->pD3D12SerializeRootSignature == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: " D3D12_SERIALIZE_ROOT_SIGNATURE_FUNC, NULL);
    }""",
        """#if defined(SDL_PLATFORM_WINRT)
    pD3D12CreateDevice = D3D12CreateDevice;
    renderer->pD3D12SerializeRootSignature = (PFN_D3D12_SERIALIZE_ROOT_SIGNATURE)D3D12SerializeRootSignature;
#else
    // Load the D3D library
    renderer->d3d12_dll = SDL_LoadObject(D3D12_DLL);
    if (renderer->d3d12_dll == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not find " D3D12_DLL, NULL);
    }

    // Load the CreateDevice function
#if (defined(SDL_PLATFORM_XBOXONE) || defined(SDL_PLATFORM_XBOXSERIES))
    D3D12XboxCreateDeviceFunc = (PFN_D3D12_XBOX_CREATE_DEVICE)SDL_LoadFunction(
        renderer->d3d12_dll,
        "D3D12XboxCreateDevice");
    if (D3D12XboxCreateDeviceFunc == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: D3D12XboxCreateDevice", NULL);
    }
#else
    pD3D12CreateDevice = (PFN_D3D12_CREATE_DEVICE)SDL_LoadFunction(
        renderer->d3d12_dll,
        D3D12_CREATE_DEVICE_FUNC);
    if (pD3D12CreateDevice == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: " D3D12_CREATE_DEVICE_FUNC, NULL);
    }
#endif

    renderer->pD3D12SerializeRootSignature = (PFN_D3D12_SERIALIZE_ROOT_SIGNATURE)SDL_LoadFunction(
        renderer->d3d12_dll,
        D3D12_SERIALIZE_ROOT_SIGNATURE_FUNC);
    if (renderer->pD3D12SerializeRootSignature == NULL) {
        D3D12_INTERNAL_DestroyRenderer(renderer);
        SET_STRING_ERROR_AND_RETURN("Could not load function: " D3D12_SERIALIZE_ROOT_SIGNATURE_FUNC, NULL);
    }
#endif"""
    )
    print("All SDL3 UWP patches applied successfully!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: patch_sdl3.py <SDL3_SOURCE_DIR>")
        sys.exit(1)
    main(sys.argv[1])
