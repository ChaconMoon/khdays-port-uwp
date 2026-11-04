/* WinRT / UWP entry point for Kingdom Hearts 358/2 Days on Xbox Series X / S.
 * This file is compiled with C++/CX (/ZW) to provide the WinRT metadata and
 * the IFrameworkView/CoreApplication initialization via SDL3.
 */

#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>

#ifdef main
#undef main
#endif

namespace khdays {
    public ref class App sealed {
    public:
        App() {}
    };
}

extern "C" int SDL_main(int argc, char *argv[]);

#if _MSVC_LANG < 202002L
extern "C" int main(int argc, char *argv[])
{
    return SDL_RunApp(argc, argv, SDL_main, NULL);
}
#endif
